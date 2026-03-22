#pragma once

#include "SimConfig.h"
#include <random>
#include <vector>
#include <optional>
#include <chrono>

// ---------------------------------------------------------------------------
// Output structs
// ---------------------------------------------------------------------------

struct OptionQuote {
    double ts;               // unix timestamp of the tick
    double expiry_ts;        // unix timestamp of option expiry
    double time_to_expiry;   // years
    double underlying_price;
    double strike;
    bool   is_call;
    double mid_px;
    double implied_vol;
    double delta, gamma, vega, theta;
};

struct TradeEvent {
    double ts;
    double price;
    int    qty;
    bool   is_buy;
};

struct MarketTickEvent {
    double ts;
    double mid, bid, ask;
    int    bid_size, ask_size;
};

struct SimStep {
    MarketTickEvent        tick;
    std::vector<TradeEvent>   trades;
    std::vector<OptionQuote>  options;
};

// ---------------------------------------------------------------------------
// Black-Scholes
// ---------------------------------------------------------------------------

struct BSGreeks {
    double price, delta, gamma, vega, theta, implied_vol;
};

BSGreeks black_scholes_greeks(double spot, double strike, double rate,
                               double dividend, double vol, double ttm,
                               bool is_call);

// ---------------------------------------------------------------------------
// Jump-diffusion price process
// ---------------------------------------------------------------------------

class JumpDiffusion {
public:
    JumpDiffusion(const MarketSimConfig& cfg, std::mt19937_64& rng);
    double step(double spot);

private:
    const MarketSimConfig& _cfg;
    std::mt19937_64&       _rng;
};

// ---------------------------------------------------------------------------
// EWMA vol + option chain generator
// ---------------------------------------------------------------------------

class OptionChainGenerator {
public:
    OptionChainGenerator(const OptionChainConfig& cfg, std::mt19937_64& rng);

    void update_vol(double price, double dt);
    bool ready() const;
    std::optional<double> current_vol() const;

    std::vector<OptionQuote> build_chain(double spot, double ts);

private:
    double _ewma_alpha(double dt) const;

    const OptionChainConfig& _cfg;
    std::mt19937_64&         _rng;

    std::optional<double> _last_price;
    std::optional<double> _ewma_var;
    int                   _n_samples = 0;
};

// ---------------------------------------------------------------------------
// Top-level simulator
// ---------------------------------------------------------------------------

class MarketSimulator {
public:
    explicit MarketSimulator(double initial_price = 100.0,
                             MarketSimConfig cfg  = MarketSimConfig(),
                             std::optional<uint64_t> seed = std::nullopt);

    SimStep step();

    double current_price() const { return _price; }
    double sim_time()      const { return _t; }

private:
    MarketTickEvent _quote_from_price(double price);

    MarketSimConfig        _cfg;
    std::mt19937_64        _rng;
    JumpDiffusion          _jump;
    OptionChainGenerator   _chain;

    double _price;
    double _t = 0.0;
    std::chrono::system_clock::time_point _ref_time;
};
