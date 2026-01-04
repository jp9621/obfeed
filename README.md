# HFT Engine

A high-performance, real-time order book created in C++ and a simulator with a web interface created in Python.

## Live Demo

Live demo at [hft-engine.onrender.com](https://hft-engine.onrender.com)

> **Note**: The demo takes approximately 30 seconds to load on first visit due to Render's cold start policy.

## Features

- Simulated market generated using jump diffusion price modeling and orders using the Hawkes process 
- Real-time order book, price chart, and trade tape visualization
- Shared and unique locks used to allow concurrent reads and thread-safe writes in order book.

## Order Types

1. **Market Orders**
   - Immediate execution at the best available price
   - No price specified, takes liquidity from the order book

2. **Limit Orders**
   - Execution at a specified price or better
   - Adds liquidity to the order book
   - Can be partially filled

3. **Iceberg Orders**
   - Large orders split into smaller visible portions
   - Helps hide true order size
   - Maintains a specified display size

4. **TWAP (Time-Weighted Average Price) Orders**
   - Splits orders into equal slices over a specified duration
   - Reduces market impact
   - Configurable number of slices

5. **Trickle Orders**
   - Random-sized slices with random time intervals
   - More natural order flow appearance
   - Configurable min/max slice sizes and pause times