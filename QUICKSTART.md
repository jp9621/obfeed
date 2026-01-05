# Quick Start Guide

OBFeed is a synthetic market data feed service that runs as a containerized service, similar to real market data providers. The feed automatically starts when the service launches.

## Quick Start with Docker

```bash
# Clone the repository
git clone <repository-url>
cd obfeed

# Start the service (feed starts automatically)
docker-compose up

# Or build and run manually
docker build -t obfeed .
docker run -p 8000:8000 obfeed
```

The service will be available at `http://localhost:8000` and the market feed starts automatically.

## Using the API

The feed is already running - no need to start it manually!

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

## Configuration (Optional)

You can configure the feed by sending a POST request to `/feed/start` with custom settings. If not configured, defaults are used.

```bash
curl -X POST http://localhost:8000/feed/start \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "symbol": "SPY",
      "initial_price": 450.0,
      "rng_seed": 42
    }
  }'
```

Note: The feed auto-starts with defaults. Use this endpoint only if you want to restart with different configuration.

## Next Steps

- Explore the [examples](examples/) directory
- Visit `http://localhost:8000/docs` for interactive API documentation
