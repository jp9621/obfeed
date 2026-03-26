# OBFeed

C++ order book and synthetic market data feed. Extension of [hft-engine](https://github.com/jp9621/hft-engine).

## Order Book

Uses a pre-allocated flat array of price levels indexed by tick offsets from a reference price. Each level holds two ring buffers (bids and asks) with per-level mutexes for fine-grained locking. Best bid/ask are tracked via atomics for lock-free reads.

- Limit orders insert into the ring buffer at their tick level; if price crosses the spread, matching runs immediately
- Market orders walk levels in price-time priority, consuming resting orders and returning a list of fills
- Cancellations are O(1) via order ID hash map; emptied levels trigger a best price refresh

## Feed Generation

Price evolves via a jump-diffusion process: geometric Brownian motion with drift plus Poisson-distributed jumps of lognormal size. Each simulation step produces bid/ask quotes around the mid-price and a Poisson-sampled set of trade events.

Option chains are generated from Black-Scholes with a configurable volatility surface: EWMA realized vol updated each step, with smile (moneyness skew) and term structure adjustments across a 21-strike × 9-expiry grid.

