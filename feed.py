from __future__ import annotations

import argparse
import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

import numpy as np
from prometheus_client import start_http_server

from bus.config import BusConfig
from bus.producer import JsonProducer
from bus.schemas import DummySchemaRegistry
from bus.topics import OPTION_TOPIC, QUOTE_TOPIC, TRADE_TOPIC
from bus.metrics import metrics


Side = Literal["BUY", "SELL"]

SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60

@dataclass
class OptionChainConfig:
    # expiries in days
    expiries_days: List[float] = field(
        default_factory=lambda: [7, 14, 21, 30, 45, 60, 75, 90, 120]
    )

    # strike offsets vs spot: K = spot * (1 + m)
    moneyness: List[float] = field(
        default_factory=lambda: list(np.linspace(-0.5, 0.5, 21))
    )

    risk_free_rate: float = 0.01
    dividend_yield: float = 0.0

    vol_ewma_halflife: float = 120.0  # seconds
    min_history_samples: int = 10
    min_vol: float = 1e-4
    max_vol: float = 1.0
    max_chain_points: Optional[int] = None

    smile_slope: float = -0.15
    smile_convexity: float = 0.10

    term_structure_slope: float = 0.02
    term_structure_pivot_days: float = 30.0

    vol_noise_std: float = 0.0

    price_noise_std: float = 0.0


@dataclass
class MarketSimConfig:
    # jump diffusion parameters
    mu: float = 0.0
    sigma: float = 3.5e-5
    jump_intensity: float = 2e-7
    jump_mu: float = 0.0
    jump_sigma: float = 0.02
    dt: float = 1.0  # seconds

    trade_intensity: float = 1.0  # expected trades per second

    tick_size: float = 0.01
    typical_trade_qty: int = 100

    quoted_spread_bps: float = 5.0  # e.g. 5 bps ~ 0.0005

    option_chain: OptionChainConfig = field(
        default_factory=lambda: OptionChainConfig(
            price_noise_std=0.02,
        )
    )


@dataclass
class TradeEvent:
    ts: str
    price: float
    qty: int
    side: Side


@dataclass
class MarketTickEvent:
    ts: str
    mid: float
    bid: float
    ask: float
    bid_size: int
    ask_size: int


class JumpDiffusion:
    def __init__(self, cfg: MarketSimConfig, rng: Optional[np.random.Generator] = None):
        self.cfg = cfg
        self.rng = rng or np.random.default_rng()

    def step(self, spot: float) -> float:
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
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _std_normal_pdf(x: float) -> float:
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
    # returns price, delta, gamma, vega, theta, implied_vol in a dict

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
    def __init__(self, cfg: OptionChainConfig, rng: Optional[np.random.Generator] = None):
        self.cfg = cfg
        self.rng = rng or np.random.default_rng()

        self._last_price: Optional[float] = None
        self._ewma_var: Optional[float] = None
        self._n_samples: int = 0

    def _ewma_alpha(self, dt: float) -> float:
        half_life = max(self.cfg.vol_ewma_halflife, 1e-6)
        # convert halflife into decay rate
        return 1.0 - math.exp(-dt / half_life)

    def update_vol(self, price: float, dt: float) -> None:
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
        return self._ewma_var is not None and self._n_samples >= self.cfg.min_history_samples

    def current_vol(self) -> Optional[float]:
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
        # map base EWMA vol into a local vol for this (strike, expiry)
        # combines: term structure, smile/skew, and vol level noise

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
        if spot <= 0:
            return None
        if not self.ready():
            return None

        base_vol = self.current_vol()
        if base_vol is None:
            return None

        with metrics.time_histogram("feed_option_chain_build_duration", engine="feed"):
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
                            metrics.set_gauge("feed_option_chain_expiries", n_expiries, engine="feed")
                            metrics.set_gauge("feed_option_chain_moneyness_points", n_moneyness, engine="feed")
                            metrics.set_gauge("feed_option_chain_total_options", len(chain), engine="feed")
                            return chain

            # Record metrics after building full chain
            metrics.set_gauge("feed_option_chain_expiries", n_expiries, engine="feed")
            metrics.set_gauge("feed_option_chain_moneyness_points", n_moneyness, engine="feed")
            metrics.set_gauge("feed_option_chain_total_options", len(chain), engine="feed")
            return chain


class MarketSimulator:
    """
    Minimal single-underlier simulator:

      * Underlying mid follows a jump diffusion.
      * Top-of-book quote is mid ± simple spread.
      * Trades arrive as a Poisson process around the mid.
      * An option chain is generated from an EWMA vol surface.
    """
    # underlying mid follows jump diffusion, option chain is generated from an EWMA vol surface

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
        dt = self.reference_time + timedelta(seconds=self.t)
        return dt.isoformat()

    def _quote_from_price(self, price: float) -> MarketTickEvent:
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
        # create tick and trades
        self.t += self.cfg.dt
        self.current_price = self.jump_process.step(self.current_price)
        mid = self.current_price

        # Update metrics for current price and volatility
        metrics.set_gauge("feed_underlying_price", mid, engine="feed")
        current_vol = self.option_chain.current_vol()
        if current_vol is not None:
            metrics.set_gauge("feed_ewma_volatility", current_vol, engine="feed")

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

        # Record metrics for trades and options generated
        metrics.inc_counter("feed_trades_generated", by=n_trades, engine="feed")
        if options:
            metrics.inc_counter("feed_quotes_generated", by=1, engine="feed")
            metrics.set_gauge("feed_options_generated_per_step", len(options), engine="feed")
        else:
            metrics.set_gauge("feed_options_generated_per_step", 0, engine="feed")

        debug = {
            "mid": mid,
            "n_trades": n_trades,
            "est_vol": self.option_chain.current_vol(),
        }

        return {
            "tick": tick,
            "trades": trades,
            "options": options,
            "debug": debug,
        }


class MarketSimFeed:
    # runs market simular and publishes json messages to kafka

    def __init__(
        self,
        symbol: str,
        venue: str = "SIM",
        wall_clock_sleep: float = 0.01,
        rng_seed: Optional[int] = None,
        producer: Optional[JsonProducer] = None,
    ):
        self.symbol = symbol
        self.venue = venue
        self.wall_clock_sleep = wall_clock_sleep

        # simulation engine
        self.sim = MarketSimulator(initial_price=100.0, rng_seed=rng_seed)

        # producer wired to kafka
        if producer is None:
            cfg = BusConfig()
            registry = DummySchemaRegistry()
            self.producer = JsonProducer(cfg, registry)
        else:
            self.producer = producer

        self.quote_topic = QUOTE_TOPIC
        self.trade_topic = TRADE_TOPIC
        self.option_topic = OPTION_TOPIC

    def _quote_msg(self, tick: MarketTickEvent) -> Dict[str, Any]:
        return {
            "type": "quote",
            "symbol": self.symbol,
            "venue": self.venue,
            "ts": tick.ts,
            "bid_px": tick.bid,
            "bid_sz": tick.bid_size,
            "ask_px": tick.ask,
            "ask_sz": tick.ask_size,
            "mid_px": tick.mid,
        }

    def _trade_msg(self, tr: TradeEvent) -> Dict[str, Any]:
        return {
            "type": "trade",
            "symbol": self.symbol,
            "venue": self.venue,
            "ts": tr.ts,
            "price": tr.price,
            "qty": tr.qty,
            "side": tr.side,
        }

    def _option_quote_msg(self, quote: Dict[str, Any]) -> Dict[str, Any]:
        opt_type = quote.get("option_type", "CALL")
        opt_code = "C" if opt_type == "CALL" else "P"

        strike = float(quote.get("strike", 0.0))
        mid_px = float(quote.get("mid_px", 0.0))

        # quote level noise
        noise_std = self.sim.cfg.option_chain.price_noise_std
        if noise_std > 0.0:
            rel_noise = self.sim.rng.normal(0.0, noise_std)
            mid_px = max(mid_px * (1.0 + rel_noise), 0.0)

        base_spread = 0.01
        spread = max(
            base_spread,
            0.01 * mid_px,
        )
        bid_px = max(mid_px - 0.5 * spread, 0.0)
        ask_px = mid_px + 0.5 * spread

        ts_iso = quote.get("ts", "")
        expiry_iso = quote.get("expiry", "")

        if expiry_iso:
            expiry_dt = datetime.fromisoformat(expiry_iso)
            expiry_str = expiry_dt.strftime("%Y%m%d")
            symbol = f"{self.symbol}-{expiry_str}-{opt_code}-{strike:.2f}"
        else:
            symbol = f"{self.symbol}-{opt_code}-{strike:.2f}"

        return {
            "type": "option_quote",
            "symbol": symbol,
            "underlier": self.symbol,
            "venue": self.venue,
            "ts": ts_iso,
            "expiry": expiry_iso,
            "time_to_expiry": quote.get("time_to_expiry"),
            "option_type": opt_code,
            "strike": strike,
            "mid_px": mid_px,
            "bid_px": bid_px,
            "ask_px": ask_px,
            "implied_vol": quote.get("implied_vol"),
            "delta": quote.get("delta"),
            "gamma": quote.get("gamma"),
            "vega": quote.get("vega"),
            "theta": quote.get("theta"),
            "underlying_price": quote.get("underlying_price"),
        }

    def run(self, n_steps: Optional[int] = None) -> None:
        step = 0

        while n_steps is None or step < n_steps:
            with metrics.time_histogram("feed_step_duration", engine="feed"):
                out = self.sim.step()
                tick: MarketTickEvent = out["tick"]
                trades: List[TradeEvent] = out["trades"]
                options: Optional[List[Dict[str, Any]]] = out.get("options")

                self.producer.produce(
                    topic=self.quote_topic,
                    key=self.symbol.encode("utf-8"),
                    payload=self._quote_msg(tick),
                )

                for tr in trades:
                    self.producer.produce(
                        topic=self.trade_topic,
                        key=self.symbol.encode("utf-8"),
                        payload=self._trade_msg(tr),
                    )

                if options:
                    for opt in options:
                        self.producer.produce(
                            topic=self.option_topic,
                            key=self.symbol.encode("utf-8"),
                            payload=self._option_quote_msg(opt),
                        )

                self.producer.poll(0)

            if self.wall_clock_sleep > 0:
                time.sleep(self.wall_clock_sleep)

            step += 1
            metrics.inc_counter("feed_simulation_steps", engine="feed")

        self.producer.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simplified market simulator → Kafka feed")
    parser.add_argument("--symbol", default="SPY", help="underlier symbol")
    parser.add_argument("--venue", default="SIM", help="venue identifier")
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.01,
        help="sleep per step in seconds (0 = as fast as possible)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=0,
        help="number of steps to run (0 = infinite)",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=0,
        help="Port to expose Prometheus metrics on (0 = disabled)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    n_steps = args.steps if args.steps > 0 else None

    # Start Prometheus metrics HTTP server, if requested.
    if args.metrics_port and args.metrics_port > 0:
        # Uses the default Prometheus REGISTRY, which bus.metrics also registers into.
        start_http_server(args.metrics_port)

    feed = MarketSimFeed(
        symbol=args.symbol,
        venue=args.venue,
        wall_clock_sleep=args.sleep,
        rng_seed=args.seed,
    )
    feed.run(n_steps=n_steps)


if __name__ == "__main__":
    main()
