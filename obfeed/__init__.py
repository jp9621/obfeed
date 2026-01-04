"""
OBFeed - Synthetic Market Feed and Orderbook Engine

A library for generating synthetic market data, managing orderbooks,
and creating option chains for testing and simulation purposes.
"""

__version__ = "0.1.0"

from obfeed.engine import MarketSimulator, OptionChainGenerator, JumpDiffusion
from obfeed.orderbook import OrderBookManager
from obfeed.config import MarketSimConfig, OptionChainConfig

__all__ = [
    "MarketSimulator",
    "OptionChainGenerator",
    "JumpDiffusion",
    "OrderBookManager",
    "MarketSimConfig",
    "OptionChainConfig",
]
