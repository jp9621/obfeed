# Quick Start Guide

## Installation

### Option 1: Docker (Recommended)

```bash
# Clone the repository
git clone <repository-url>
cd obfeed

# Start with docker-compose
docker-compose up

# Or build manually
docker build -t obfeed .
docker run -p 8000:8000 obfeed
```

### Option 2: Python

```bash
# Install dependencies
pip install -r requirements.txt

# Install package
pip install -e .

# Or install directly
pip install .
```

## Running the Server

```bash
# Start API server
obfeed server

# Or with custom port
obfeed server --port 8080

# Or with auto-reload (development)
obfeed server --reload
```

The API will be available at `http://localhost:8000`

## Running Standalone Feed

```bash
# Run feed with default settings
obfeed feed

# Run with custom symbol and steps
obfeed feed --symbol SPY --steps 100

# Run with JSON output
obfeed feed --output json --steps 50

# Run with config file
obfeed feed --config-file examples/config_example.json
```

## Using the API

### Start the Feed

```bash
curl -X POST http://localhost:8000/feed/start \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "symbol": "SPY",
      "initial_price": 450.0
    }
  }'
```

### Get Current Quote

```bash
curl http://localhost:8000/feed/quote
```

### Get Option Chain

```bash
curl http://localhost:8000/feed/options
```

### Submit Order

```bash
curl -X POST http://localhost:8000/orderbook/order \
  -H "Content-Type: application/json" \
  -d '{
    "side": "BUY",
    "quantity": 100,
    "price": 450.50,
    "order_type": "LIMIT"
  }'
```

### Get Orderbook

```bash
curl http://localhost:8000/orderbook?max_levels=10
```

## WebSocket Example

```python
import asyncio
import websockets
import json

async def subscribe():
    uri = "ws://localhost:8000/ws"
    async with websockets.connect(uri) as websocket:
        while True:
            message = await websocket.recv()
            data = json.loads(message)
            if data["type"] == "update":
                print(f"Mid: {data['tick']['mid']:.2f}")

asyncio.run(subscribe())
```

## Python Library Usage

```python
from obfeed import MarketSimulator, MarketSimConfig

# Create simulator
config = MarketSimConfig(sigma=0.0001, trade_intensity=2.0)
simulator = MarketSimulator(initial_price=100.0, cfg=config)

# Run steps
for _ in range(100):
    result = simulator.step()
    tick = result["tick"]
    print(f"Mid: {tick.mid:.2f}")
```

## Next Steps

- Explore the [examples](examples/) directory
- Visit `http://localhost:8000/docs` for interactive API documentation
