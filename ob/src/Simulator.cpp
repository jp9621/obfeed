#include "Simulator.h"
#include <cmath>
#include <algorithm>

static constexpr double SECONDS_PER_YEAR = 365.25 * 24.0 * 60.0 * 60.0;


OptionChainConfig::OptionChainConfig()
    : expiries_days{7, 14, 21, 30, 45, 60, 75, 90, 120}
{
    const int n = 21;
    moneyness.resize(n);
    for (int i = 0; i < n; ++i)
        moneyness[i] = -0.5 + i * (1.0 / (n - 1));
}

MarketSimConfig::MarketSimConfig() {
    option_chain.price_noise_std = 0.02;
}


static double std_normal_cdf(double x) {
    static constexpr double p  = 0.2316419;
    static constexpr double a1 =  0.319381530;
    static constexpr double a2 = -0.356563782;
    static constexpr double a3 =  1.781477937;
    static constexpr double a4 = -1.821255978;
    static constexpr double a5 =  1.330274429;
    static constexpr double INV_SQRT2PI = 0.3989422804014327;
    bool   neg = (x < 0.0);
    double ax  = neg ? -x : x;
    double t   = 1.0 / (1.0 + p * ax);
    double poly = t * (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5))));
    double pdf  = INV_SQRT2PI * std::exp(-0.5 * ax * ax);
    double cdf  = 1.0 - pdf * poly;
    return neg ? 1.0 - cdf : cdf;
}

static double std_normal_pdf(double x) {
    static constexpr double INV_SQRT2PI = 0.3989422804014327;
    return INV_SQRT2PI * std::exp(-0.5 * x * x);
}


BSGreeks black_scholes_greeks(double spot, double strike, double rate,
                               double dividend, double vol, double ttm,
                               bool is_call)
{
    spot   = std::max(spot,   1e-12);
    strike = std::max(strike, 1e-12);
    ttm    = std::max(ttm,    0.0);
    vol    = std::max(vol,    0.0);

    if (ttm == 0.0 || vol < 1e-12) {
        double intrinsic = is_call ? std::max(spot - strike, 0.0)
                                   : std::max(strike - spot, 0.0);
        double delta = 0.0;
        if (is_call)  delta = (spot > strike) ? 1.0 : 0.0;
        else          delta = (spot < strike) ? -1.0 : 0.0;
        return {intrinsic, delta, 0.0, 0.0, 0.0, vol};
    }

    double sqrt_t       = std::sqrt(ttm);
    double sigma_sqrt_t = vol * sqrt_t;

    double d1 = (std::log(spot / strike) + (rate - dividend + 0.5 * vol * vol) * ttm)
                / sigma_sqrt_t;
    double d2 = d1 - sigma_sqrt_t;

    double Nd1    = std_normal_cdf(d1);
    double Nd2    = std_normal_cdf(d2);
    double phi_d1 = std_normal_pdf(d1);

    double disc_r = std::exp(-rate     * ttm);
    double disc_q = std::exp(-dividend * ttm);

    double price, delta, theta;

    if (is_call) {
        price = spot * disc_q * Nd1 - strike * disc_r * Nd2;
        delta = disc_q * Nd1;
        theta = -spot * disc_q * phi_d1 * vol / (2.0 * sqrt_t)
                - rate * strike * disc_r * Nd2
                + dividend * spot * disc_q * Nd1;
    } else {
        price = strike * disc_r * std_normal_cdf(-d2) - spot * disc_q * std_normal_cdf(-d1);
        delta = disc_q * (Nd1 - 1.0);
        theta = -spot * disc_q * phi_d1 * vol / (2.0 * sqrt_t)
                + rate * strike * disc_r * std_normal_cdf(-d2)
                - dividend * spot * disc_q * std_normal_cdf(-d1);
    }

    double gamma = disc_q * phi_d1 / (spot * sigma_sqrt_t);
    double vega  = spot * disc_q * phi_d1 * sqrt_t;

    return {price, delta, gamma, vega, theta, vol};
}


JumpDiffusion::JumpDiffusion(const MarketSimConfig& cfg, std::mt19937_64& rng)
    : _cfg(cfg), _rng(rng) {}

double JumpDiffusion::step(double spot) {
    if (spot <= 0.0) spot = 1e-6;

    std::normal_distribution<double> z_dist(0.0, 1.0);
    double z         = z_dist(_rng);
    double drift     = (_cfg.mu - 0.5 * _cfg.sigma * _cfg.sigma) * _cfg.dt;
    double diffusion = _cfg.sigma * std::sqrt(_cfg.dt) * z;

    std::poisson_distribution<int> pois(_cfg.jump_intensity * _cfg.dt);
    int n_jumps = pois(_rng);

    double jump_term = 0.0;
    if (n_jumps > 0) {
        std::normal_distribution<double> jdist(_cfg.jump_mu, _cfg.jump_sigma);
        for (int i = 0; i < n_jumps; ++i)
            jump_term += jdist(_rng);
    }

    return spot * std::exp(drift + diffusion + jump_term);
}


OptionChainGenerator::OptionChainGenerator(const OptionChainConfig& cfg,
                                            std::mt19937_64& rng)
    : _cfg(cfg), _rng(rng) {}

double OptionChainGenerator::_ewma_alpha(double dt) const {
    double half_life = std::max(_cfg.vol_ewma_halflife, 1e-6);
    return 1.0 - std::exp(-dt / half_life);
}

void OptionChainGenerator::update_vol(double price, double dt) {
    if (price <= 0.0) return;

    if (_last_price.has_value() && dt > 0.0) {
        double log_ret  = std::log(price / *_last_price);
        double inst_var = (log_ret * log_ret) * SECONDS_PER_YEAR / std::max(dt, 1e-6);

        double a = _ewma_alpha(dt);
        if (!_ewma_var.has_value())
            _ewma_var = inst_var;
        else
            _ewma_var = a * inst_var + (1.0 - a) * (*_ewma_var);
        ++_n_samples;
    }

    _last_price = price;
}

bool OptionChainGenerator::ready() const {
    return _ewma_var.has_value() && _n_samples >= _cfg.min_history_samples;
}

std::optional<double> OptionChainGenerator::current_vol() const {
    if (!_ewma_var.has_value()) return std::nullopt;
    double vol = std::sqrt(std::max(*_ewma_var, _cfg.min_vol * _cfg.min_vol));
    return std::min(vol, _cfg.max_vol);
}

std::vector<OptionQuote> OptionChainGenerator::build_chain(double spot, double ts) {
    if (spot <= 0.0 || !ready()) return {};

    auto vol_opt = current_vol();
    if (!vol_opt.has_value()) return {};
    double base_vol = *vol_opt;

    std::vector<OptionQuote> chain;
    chain.reserve(_cfg.expiries_days.size() * _cfg.moneyness.size() * 2);
    const double secs_per_day  = 86400.0;
    static constexpr double INV_SQRT2PI = 0.3989422804014327;
    const double r = _cfg.risk_free_rate;
    const double q = _cfg.dividend_yield;

    std::normal_distribution<double> noise_dist(0.0, _cfg.vol_noise_std);

    for (double days : _cfg.expiries_days) {
        days = std::max(days, 1e-6);
        double ttm       = days / 365.25;
        double expiry_ts = ts + days * secs_per_day;

        double sqrt_t    = std::sqrt(ttm);
        double disc_r    = std::exp(-r * ttm);
        double disc_q    = std::exp(-q * ttm);
        double pivot     = std::max(_cfg.term_structure_pivot_days, 1e-6);
        double term_scale = std::max(1.0 + _cfg.term_structure_slope * (days - pivot) / pivot, 0.1);

        for (double m : _cfg.moneyness) {
            double strike = std::max(spot * (1.0 + m), 1e-6);

            double lk = (spot > 0.0 && strike > 0.0) ? std::log(strike / spot) : 0.0;
            double smile_f = std::clamp(
                1.0 + _cfg.smile_slope * lk + _cfg.smile_convexity * lk * lk, 0.2, 5.0);
            double lv = base_vol * term_scale * smile_f;
            if (_cfg.vol_noise_std > 0.0) lv += noise_dist(_rng);
            lv = std::clamp(lv, _cfg.min_vol, _cfg.max_vol);

            double call_price, call_delta, call_theta;
            double put_price,  put_delta,  put_theta;
            double gamma, vega;

            if (ttm <= 0.0 || lv < 1e-12) {
                call_price  = std::max(spot - strike, 0.0);
                put_price   = std::max(strike - spot, 0.0);
                call_delta  = (spot > strike) ? 1.0 : 0.0;
                put_delta   = (spot < strike) ? -1.0 : 0.0;
                call_theta  = put_theta = gamma = vega = 0.0;
            } else {
                double sigma_sqrt_t = lv * sqrt_t;
                double d1 = (std::log(spot / strike)
                             + (r - q + 0.5 * lv * lv) * ttm) / sigma_sqrt_t;
                double d2 = d1 - sigma_sqrt_t;

                double Nd1 = std_normal_cdf(d1);
                double Nd2 = std_normal_cdf(d2);
                double phi  = INV_SQRT2PI * std::exp(-0.5 * d1 * d1);

                double dq_s  = disc_q * spot;
                double dr_k  = disc_r * strike;

                call_price = dq_s * Nd1 - dr_k * Nd2;
                call_delta = disc_q * Nd1;
                call_theta = -dq_s * phi * lv / (2.0 * sqrt_t)
                             - r * dr_k * Nd2
                             + q * dq_s * Nd1;

                double Nd1n = 1.0 - Nd1;
                double Nd2n = 1.0 - Nd2;
                put_price  = dr_k * Nd2n - dq_s * Nd1n;
                put_delta  = disc_q * (Nd1 - 1.0);
                put_theta  = -dq_s * phi * lv / (2.0 * sqrt_t)
                             + r * dr_k * Nd2n
                             - q * dq_s * Nd1n;

                gamma = disc_q * phi / (spot * sigma_sqrt_t);
                vega  = dq_s * phi * sqrt_t;
            }

            chain.push_back({ts, expiry_ts, ttm, spot, strike, true,
                             call_price, lv, call_delta, gamma, vega, call_theta});
            chain.push_back({ts, expiry_ts, ttm, spot, strike, false,
                             put_price,  lv, put_delta,  gamma, vega, put_theta});

            if (_cfg.max_chain_points > 0 &&
                static_cast<int>(chain.size()) >= _cfg.max_chain_points)
                return chain;
        }
    }
    return chain;
}


MarketSimulator::MarketSimulator(double initial_price, MarketSimConfig cfg,
                                  std::optional<uint64_t> seed)
    : _cfg(std::move(cfg))
    , _rng(seed.has_value() ? std::mt19937_64(*seed) : std::mt19937_64(std::random_device{}()))
    , _jump(_cfg, _rng)
    , _chain(_cfg.option_chain, _rng)
    , _price(initial_price)
    , _ref_time(std::chrono::system_clock::now())
{}

MarketTickEvent MarketSimulator::_quote_from_price(double price) {
    double spread = std::max(_cfg.tick_size,
                             price * (_cfg.quoted_spread_bps / 1e4));
    double bid = std::max(0.0, price - 0.5 * spread);
    double ask = price + 0.5 * spread;

    std::uniform_int_distribution<int> sz_dist(_cfg.typical_trade_qty / 2,
                                                _cfg.typical_trade_qty * 2);
    double ts = std::chrono::duration<double>(_ref_time.time_since_epoch()).count() + _t;

    return {ts, price, bid, ask, sz_dist(_rng), sz_dist(_rng)};
}

SimStep MarketSimulator::step() {
    _t    += _cfg.dt;
    _price = _jump.step(_price);

    MarketTickEvent tick = _quote_from_price(_price);

    _chain.update_vol(_price, _cfg.dt);
    auto options = _chain.build_chain(_price, tick.ts);

    double expected_trades = std::max(_cfg.trade_intensity * _cfg.dt, 0.0);
    int n_trades = 0;
    if (expected_trades > 0.0) {
        std::poisson_distribution<int> trade_pois(expected_trades);
        n_trades = trade_pois(_rng);
    }

    std::vector<TradeEvent> trades;
    trades.reserve(n_trades);

    std::uniform_int_distribution<int> side_coin(0, 1);
    std::uniform_int_distribution<int> offset_dist(0, 2);
    std::uniform_int_distribution<int> qty_dist(1, _cfg.typical_trade_qty);

    for (int i = 0; i < n_trades; ++i) {
        bool   is_buy    = side_coin(_rng) == 1;
        int    direction = is_buy ? 1 : -1;
        int    ticks     = offset_dist(_rng);
        double tp        = std::max(_cfg.tick_size,
                                    _price + direction * ticks * _cfg.tick_size);
        trades.push_back({tick.ts, tp, qty_dist(_rng), is_buy});
    }

    return {tick, std::move(trades), std::move(options)};
}
