"""Configuration classes for market simulation."""

from dataclasses import dataclass, field
from typing import List, Optional
import numpy as np


@dataclass
class OptionChainConfig:
    """Configuration for option chain generation."""
    
    expiries_days: List[float] = field(
        default_factory=lambda: [7, 14, 21, 30, 45, 60, 75, 90, 120]
    )

    moneyness: List[float] = field(
        default_factory=lambda: list(np.linspace(-0.5, 0.5, 21))
    )

    risk_free_rate: float = 0.01
    dividend_yield: float = 0.0

    vol_ewma_halflife: float = 120.0
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
    """Configuration for market simulation."""
    
    mu: float = 0.0
    sigma: float = 3.5e-5
    jump_intensity: float = 2e-7
    jump_mu: float = 0.0
    jump_sigma: float = 0.02
    dt: float = 1.0

    trade_intensity: float = 1.0

    tick_size: float = 0.01
    typical_trade_qty: int = 100

    quoted_spread_bps: float = 5.0

    option_chain: OptionChainConfig = field(
        default_factory=lambda: OptionChainConfig(
            price_noise_std=0.02,
        )
    )


@dataclass
class EngineConfig:
    """Top-level configuration for the feed engine."""
    
    symbol: str = "SPY"
    venue: str = "SIM"
    initial_price: float = 100.0
    rng_seed: Optional[int] = None
    
    market: MarketSimConfig = field(default_factory=MarketSimConfig)
    
    enable_quotes: bool = True
    enable_trades: bool = True
    enable_options: bool = True
    
    wall_clock_sleep: float = 0.01
