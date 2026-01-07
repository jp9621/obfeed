# OBFeed

OBFeed is a synthetic market data feed service that provides realistic market data streams via REST API and WebSocket. It simulates equity markets with order books, trades, quotes, and option chains, making it ideal for testing trading algorithms, market data consumers, and financial applications without requiring access to real market data feeds.

This project is a more portable extension of the previous [hft-engine](https://github.com/jp9621/hft-engine) project, redesigned as a containerized service with a clean API interface.

## Features

- **Real-time Market Data**: Streaming quotes (bid/ask) and trades via WebSocket
- **Order Book Management**: C++-based order book with limit and market order support
- **Option Chain Generation**: Black-Scholes pricing with volatility surface modeling and Greeks
- **Market Simulation**: Jump-diffusion price process with configurable parameters
- **REST API**: HTTP endpoints for quotes, order book, options, and feed control
- **WebSocket Streaming**: Real-time updates for quotes, trades, and option chains

## Docker Installation

```bash
# Clone the repository
git clone https://github.com/jp9621/obfeed.git
cd obfeed

# Build and run with Docker Compose
docker compose up --build
```


## Usage

### Quick Start

The service starts automatically when the container launches. You can interact with it using:

**REST API:**
```bash
# Get current quote
curl http://localhost:8000/feed/quote

# Get order book snapshot
curl http://localhost:8000/orderbook?max_levels=10

# Get option chain
curl http://localhost:8000/feed/options
```

**WebSocket:**
```python
import asyncio
import websockets
import json

async def connect():
    async with websockets.connect("ws://localhost:8000/ws") as ws:
        while True:
            message = await ws.recv()
            data = json.loads(message)
            print(data)

asyncio.run(connect())
```

## API Endpoints

- `GET /` - Service status
- `GET /health` - Health check
- `GET /feed/quote` - Current market quote
- `GET /feed/options` - Option chain snapshot
- `GET /orderbook` - Order book snapshot
- `POST /orderbook/order` - Submit limit or market order
- `POST /feed/start` - Start the feed (with optional config)
- `POST /feed/stop` - Stop the feed
- `GET /feed/state` - Get feed state
- `WebSocket /ws` - Real-time streaming updates

## Configuration

The service can be configured via the `/feed/start` endpoint or environment variables. See `obfeed/config.py` for available configuration options including:

- Market simulation parameters (volatility, jump intensity, etc.)
- Option chain settings (expiries, strikes, volatility surface)
- Feed timing and rate control

## Architecture

- **Python API Layer**: FastAPI-based REST and WebSocket server
- **Market Simulation Engine**: Jump-diffusion price process with option chain generation
- **C++ Order Book**: High-performance order book implementation via pybind11
- **Docker**: Containerized deployment for easy distribution

## Requirements

- Python 3.11+
- CMake 3.15+
- C++ compiler with C++17 support
- Docker (optional, for containerized deployment)
