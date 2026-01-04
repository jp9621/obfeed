"""Core market simulation engine."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

import numpy as np

from obfeed.config import MarketSimConfig, OptionChainConfig

Side = Literal["BUY", "SELL"]
SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60


@dataclass
class TradeEvent:
    """Represents a trade event."""
    ts: str
    price: float
    qty: int
    side: Side


@dataclass
class MarketTickEvent:
    """Represents a market quote tick."""
    ts: str
    mid: float
    bid: float
    ask: float
    bid_size: int
    ask_size: int


class JumpDiffusion:
    """Jump diffusion process for price simulation."""
    
    def __init__(self, cfg: MarketSimConfig, rng: Optional[np.random.Generator] = None):
        self.cfg = cfg
        self.rng = rng or np.random.default_rng()

    def step(self, spot: float) -> float:
        """Advance the price by one step."""
        if spot <= 0:
            spot = 1e-6

        c = self.cfg

        # diffusion
        z = self.rng.normal()
        drift = (c.mu - 0.5 * c.sigma ** 2) * c.dt
        diffusion = c.sigma * math.sqrt(c.dt) * z

        # jumps
        n_jumps = self.rng.poisson(c.jump_intensity * c.dt)
        jump_term = 0.0
        if n_jumps > 0:
            jump_draws = self.rng.normal(c.jump_mu, c.jump_sigma, size=n_jumps)
            jump_term = float(jump_draws.sum())

        log_return = drift + diffusion + jump_term
        return spot * math.exp(log_return)


def _std_normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _std_normal_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_greeks(
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    vol: float,
    ttm: float,
    option_type: Literal["CALL", "PUT"],
) -> Dict[str, float]:
    """Calculate Black-Scholes option price and Greeks."""
    spot = max(spot, 1e-12)
    strike = max(strike, 1e-12)
    ttm = max(ttm, 0.0)
    vol = max(vol, 0.0)

    # early return if ttm (time to maturity) is 0 or vol is too low
    if ttm == 0.0 or vol < 1e-12:
        intrinsic = max(spot - strike, 0.0) if option_type == "CALL" else max(strike - spot, 0.0)
        delta = 1.0 if (option_type == "CALL" and spot > strike) else 0.0
        if option_type == "PUT":
            delta = -1.0 if spot < strike else 0.0
        return {
            "price": intrinsic,
            "delta": delta,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "implied_vol": vol,
        }

    sqrt_t = math.sqrt(ttm)
    sigma_sqrt_t = vol * sqrt_t

    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * vol * vol) * ttm) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t

    Nd1 = _std_normal_cdf(d1)
    Nd2 = _std_normal_cdf(d2)
    phi_d1 = _std_normal_pdf(d1)

    disc_r = math.exp(-rate * ttm)
    disc_q = math.exp(-dividend * ttm)

    if option_type == "CALL":
        price = spot * disc_q * Nd1 - strike * disc_r * Nd2
        delta = disc_q * Nd1
        theta = (
            -spot * disc_q * phi_d1 * vol / (2.0 * sqrt_t)
            - rate * strike * disc_r * Nd2
            + dividend * spot * disc_q * Nd1
        )
    else:
        price = strike * disc_r * _std_normal_cdf(-d2) - spot * disc_q * _std_normal_cdf(-d1)
        delta = disc_q * (Nd1 - 1.0)
        theta = (
            -spot * disc_q * phi_d1 * vol / (2.0 * sqrt_t)
            + rate * strike * disc_r * _std_normal_cdf(-d2)
            - dividend * spot * disc_q * _std_normal_cdf(-d1)
        )

    gamma = disc_q * phi_d1 / (spot * sigma_sqrt_t)
    vega = spot * disc_q * phi_d1 * sqrt_t

    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
        "implied_vol": vol,
    }


class OptionChainGenerator:
    """Generates option chains using Black-Scholes with volatility surface modeling."""
    
    def __init__(self, cfg: OptionChainConfig, rng: Optional[np.random.Generator] = None):
        self.cfg = cfg
        self.rng = rng or np.random.default_rng()

        self._last_price: Optional[float] = None
        self._ewma_var: Optional[float] = None
        self._n_samples: int = 0

    def _ewma_alpha(self, dt: float) -> float:
        """Calculate EWMA alpha from half-life."""
        half_life = max(self.cfg.vol_ewma_halflife, 1e-6)
        return 1.0 - math.exp(-dt / half_life)

    def update_vol(self, price: float, dt: float) -> None:
        """Update volatility estimate from price movement."""
        if price <= 0:
            return

        if self._last_price is not None and dt > 0:
            log_ret = math.log(price / self._last_price)
            inst_var = (log_ret ** 2) * SECONDS_PER_YEAR / max(dt, 1e-6)

            a = self._ewma_alpha(dt)
            if self._ewma_var is None:
                self._ewma_var = inst_var
            else:
                self._ewma_var = a * inst_var + (1.0 - a) * self._ewma_var
            self._n_samples += 1

        self._last_price = price

    def ready(self) -> bool:
        """Check if enough history for volatility estimation."""
        return self._ewma_var is not None and self._n_samples >= self.cfg.min_history_samples

    def current_vol(self) -> Optional[float]:
        """Get current volatility estimate."""
        if self._ewma_var is None:
            return None
        vol = math.sqrt(max(self._ewma_var, self.cfg.min_vol ** 2))
        return min(vol, self.cfg.max_vol)

    def _local_vol(
        self,
        spot: float,
        strike: float,
        days_to_expiry: float,
        base_vol: float,
    ) -> float:
        """Calculate local volatility for a specific strike and expiry."""
        c = self.cfg

        # term structure adjustment
        pivot = max(c.term_structure_pivot_days, 1e-6)
        rel = (days_to_expiry - pivot) / pivot
        term_scale = 1.0 + c.term_structure_slope * rel
        term_scale = max(term_scale, 0.1)

        vol = base_vol * term_scale

        # smile/skew in log-moneyness
        if spot > 0.0 and strike > 0.0:
            k = math.log(strike / spot)
        else:
            k = 0.0

        smile_factor = 1.0 + c.smile_slope * k + c.smile_convexity * (k ** 2)
        smile_factor = min(max(smile_factor, 0.2), 5.0)
        vol *= smile_factor

        # vol level noise
        if c.vol_noise_std > 0.0:
            vol += float(self.rng.normal(0.0, c.vol_noise_std))

        # clamp
        vol = max(vol, c.min_vol)
        vol = min(vol, c.max_vol)
        return vol

    def build_chain(self, spot: float, ts_iso: str) -> Optional[List[Dict[str, Any]]]:
        """Build option chain for current spot price."""
        if spot <= 0:
            return None
        if not self.ready():
            return None

        base_vol = self.current_vol()
        if base_vol is None:
            return None

        ts_dt = datetime.fromisoformat(ts_iso)
        chain: List[Dict[str, Any]] = []
        n_expiries = len(self.cfg.expiries_days)
        n_moneyness = len(self.cfg.moneyness)

        # for each expiry
        for days in self.cfg.expiries_days:
            days = max(days, 1e-6)
            ttm_years = days / 365.25
            expiry_dt = ts_dt + timedelta(days=days)
            expiry_iso = expiry_dt.isoformat()

            # and for each moneyness
            for m in self.cfg.moneyness:
                strike = max(spot * (1.0 + m), 1e-6)
                # calculate the local vol for this (strike, expiry)
                local_vol = self._local_vol(
                    spot=spot,
                    strike=strike,
                    days_to_expiry=days,
                    base_vol=base_vol,
                )

                # and for each option type
                for opt_type in ("CALL", "PUT"):
                    # price the option using Black-Scholes
                    greeks = black_scholes_greeks(
                        spot=spot,
                        strike=strike,
                        rate=self.cfg.risk_free_rate,
                        dividend=self.cfg.dividend_yield,
                        vol=local_vol,
                        ttm=ttm_years,
                        option_type=opt_type,
                    )

                    quote = {
                        "ts": ts_iso,
                        "expiry": expiry_iso,
                        "time_to_expiry": ttm_years,
                        "underlying_price": spot,
                        "strike": strike,
                        "option_type": opt_type,
                        "mid_px": greeks["price"],
                        "implied_vol": local_vol,
                        "delta": greeks["delta"],
                        "gamma": greeks["gamma"],
                        "vega": greeks["vega"],
                        "theta": greeks["theta"],
                    }
                    chain.append(quote)

                    if self.cfg.max_chain_points is not None and len(chain) >= self.cfg.max_chain_points:
                        return chain

        return chain


class MarketSimulator:
    """
    Market simulator for single underlying:
    
    * Underlying mid follows a jump diffusion
    * Top-of-book quote is mid ± simple spread
    * Trades arrive as a Poisson process around the mid
    * Option chain is generated from an EWMA vol surface
    """
    
    def __init__(
        self,
        initial_price: float = 100.0,
        cfg: Optional[MarketSimConfig] = None,
        rng_seed: Optional[int] = None,
    ):
        if rng_seed is not None:
            np.random.seed(rng_seed)
            random.seed(rng_seed)

        self.cfg = cfg or MarketSimConfig()
        self.rng = np.random.default_rng(rng_seed)

        self.jump_process = JumpDiffusion(self.cfg, rng=self.rng)
        self.option_chain = OptionChainGenerator(self.cfg.option_chain, rng=self.rng)

        self.current_price = initial_price
        self.t = 0.0  # simulation time in seconds
        self.reference_time = datetime.now()

    def _iso_ts(self) -> str:
        """Get ISO timestamp for current simulation time."""
        dt = self.reference_time + timedelta(seconds=self.t)
        return dt.isoformat()

    def _quote_from_price(self, price: float) -> MarketTickEvent:
        """Generate quote from mid price."""
        spread = max(
            self.cfg.tick_size,
            price * (self.cfg.quoted_spread_bps / 1e4),
        )
        bid = max(0.0, price - 0.5 * spread)
        ask = price + 0.5 * spread

        bid_sz = random.randint(self.cfg.typical_trade_qty // 2, self.cfg.typical_trade_qty * 2)
        ask_sz = random.randint(self.cfg.typical_trade_qty // 2, self.cfg.typical_trade_qty * 2)

        return MarketTickEvent(
            ts=self._iso_ts(),
            mid=price,
            bid=bid,
            ask=ask,
            bid_size=bid_sz,
            ask_size=ask_sz,
        )

    def step(self) -> Dict[str, Any]:
        """Advance simulation by one step."""
        # create tick and trades
        self.t += self.cfg.dt
        self.current_price = self.jump_process.step(self.current_price)
        mid = self.current_price

        tick = self._quote_from_price(mid)

        self.option_chain.update_vol(price=mid, dt=self.cfg.dt)

        options = self.option_chain.build_chain(spot=mid, ts_iso=tick.ts)

        expected_trades = max(self.cfg.trade_intensity * self.cfg.dt, 0.0)
        n_trades = self.rng.poisson(expected_trades) if expected_trades > 0 else 0

        trades: List[TradeEvent] = []
        for _ in range(n_trades):
            side: Side = "BUY" if random.random() < 0.5 else "SELL"
            direction = 1 if side == "BUY" else -1

            price_offset_ticks = self.rng.integers(low=0, high=3)
            trade_price = mid + direction * price_offset_ticks * self.cfg.tick_size
            trade_price = max(self.cfg.tick_size, trade_price)

            qty = random.randint(1, self.cfg.typical_trade_qty)

            trades.append(
                TradeEvent(
                    ts=tick.ts,
                    price=trade_price,
                    qty=qty,
                    side=side,
                )
            )

        return {
            "tick": tick,
            "trades": trades,
            "options": options,
            "debug": {
                "mid": mid,
                "n_trades": n_trades,
                "est_vol": self.option_chain.current_vol(),
            },
        }
