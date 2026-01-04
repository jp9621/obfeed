"""Example API client for OBFeed."""

import requests
import json
import time

BASE_URL = "http://localhost:8000"

def start_feed():
    """Start the market feed."""
    response = requests.post(
        f"{BASE_URL}/feed/start",
        json={
            "config": {
                "symbol": "SPY",
                "venue": "SIM",
                "initial_price": 450.0,
                "rng_seed": 42,
            }
        }
    )
    print(f"Start feed: {response.status_code}")
    print(json.dumps(response.json(), indent=2))

def get_quote():
    """Get current market quote."""
    response = requests.get(f"{BASE_URL}/feed/quote")
    print("\nCurrent Quote:")
    print(json.dumps(response.json(), indent=2))

def get_options():
    """Get option chain."""
    response = requests.get(f"{BASE_URL}/feed/options")
    data = response.json()
    print(f"\nOption Chain ({len(data['options'])} options):")
    # Show first 5 options
    for opt in data["options"][:5]:
        print(f"  {opt['option_type']} {opt['strike']:.2f}: ${opt['mid_px']:.2f}")

def submit_order():
    """Submit a limit order."""
    response = requests.post(
        f"{BASE_URL}/orderbook/order",
        json={
            "side": "BUY",
            "quantity": 100,
            "price": 450.50,
            "order_type": "LIMIT",
        }
    )
    print("\nOrder Submission:")
    print(json.dumps(response.json(), indent=2))

def get_orderbook():
    """Get orderbook snapshot."""
    response = requests.get(f"{BASE_URL}/orderbook?max_levels=5")
    print("\nOrderbook:")
    print(json.dumps(response.json(), indent=2))

def stop_feed():
    """Stop the feed."""
    response = requests.post(f"{BASE_URL}/feed/stop")
    print(f"\nStop feed: {response.status_code}")
    print(json.dumps(response.json(), indent=2))

if __name__ == "__main__":
    print("OBFeed API Client Example")
    print("=" * 50)
    
    # Start feed
    start_feed()
    time.sleep(2)  # Wait for feed to generate some data
    
    # Get quote
    get_quote()
    
    # Get options
    get_options()
    
    # Submit order
    submit_order()
    
    # Get orderbook
    get_orderbook()
    
    # Stop feed
    stop_feed()
