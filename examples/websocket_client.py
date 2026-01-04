"""WebSocket client example for OBFeed."""

import asyncio
import websockets
import json

async def subscribe():
    """Subscribe to WebSocket feed."""
    uri = "ws://localhost:8000/ws"
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to OBFeed WebSocket")
            print("Waiting for updates...\n")
            
            message_count = 0
            while message_count < 10:  # Receive 10 updates
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    data = json.loads(message)
                    
                    if data.get("type") == "update":
                        tick = data["tick"]
                        trades = data["trades"]
                        options = data.get("options", [])
                        
                        message_count += 1
                        print(f"Update #{message_count}:")
                        print(f"  Time: {tick['ts']}")
                        print(f"  Mid: ${tick['mid']:.2f}")
                        print(f"  Bid: ${tick['bid']:.2f} ({tick['bid_size']})")
                        print(f"  Ask: ${tick['ask']:.2f} ({tick['ask_size']})")
                        print(f"  Trades: {len(trades)}")
                        print(f"  Options: {len(options)}")
                        print()
                    
                    elif data.get("type") == "state":
                        print("Initial state received:")
                        print(json.dumps(data["data"], indent=2))
                        print()
                        
                except asyncio.TimeoutError:
                    print("Timeout waiting for message")
                    break
                    
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("OBFeed WebSocket Client Example")
    print("=" * 50)
    asyncio.run(subscribe())
