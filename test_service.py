#!/usr/bin/env python3
"""
Test script for the Market Feed Service.
Tests all REST API endpoints and provides basic validation.

Dependencies:
    pip install requests websockets
"""

import requests
import time
import json
import asyncio
from typing import Dict, Any, List

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("Warning: websockets library not available. WebSocket tests will be skipped.")
    print("Install with: pip install websockets")

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"
TIMEOUT = 5


def test_health() -> bool:
    """Test the health endpoint."""
    print("=" * 60)
    print("Testing /health endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        print(f"✓ Health check passed")
        print(f"  Status: {data.get('status')}")
        print(f"  Orderbook initialized: {data.get('orderbook_initialized')}")
        print(f"  Simulator running: {data.get('simulator_running')}")
        return True
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return False


def test_quote() -> bool:
    """Test the quote endpoint."""
    print("\n" + "=" * 60)
    print("Testing /quote endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/quote", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        print(f"✓ Quote retrieved")
        print(f"  Bid: ${data.get('bid', 0):.2f} (size: {data.get('bid_size', 0)})")
        print(f"  Ask: ${data.get('ask', 0):.2f} (size: {data.get('ask_size', 0)})")
        print(f"  Mid: ${data.get('mid', 0):.2f}")
        print(f"  Timestamp: {data.get('ts', 'N/A')}")
        return True
    except Exception as e:
        print(f"✗ Quote retrieval failed: {e}")
        return False


def test_orderbook(depth: int = 5) -> bool:
    """Test the orderbook endpoint."""
    print("\n" + "=" * 60)
    print(f"Testing /orderbook endpoint (depth={depth})...")
    try:
        response = requests.get(f"{BASE_URL}/orderbook", params={"depth": depth}, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        print(f"✓ Orderbook retrieved")
        print(f"  Best Bid: ${data.get('best_bid', 0):.2f}")
        print(f"  Best Ask: ${data.get('best_ask', 0):.2f}")
        print(f"  Mid: ${data.get('mid', 0):.2f}")
        print(f"  Spread: ${data.get('spread', 0):.4f}")
        
        bids = data.get('bids', [])
        asks = data.get('asks', [])
        print(f"\n  Top {min(depth, len(bids))} Bids:")
        for bid in bids[:depth]:
            print(f"    ${bid['price']:.2f} x {bid['quantity']}")
        
        print(f"\n  Top {min(depth, len(asks))} Asks:")
        for ask in asks[:depth]:
            print(f"    ${ask['price']:.2f} x {ask['quantity']}")
        
        return True
    except Exception as e:
        print(f"✗ Orderbook retrieval failed: {e}")
        return False


def test_options() -> bool:
    """Test the options endpoint."""
    print("\n" + "=" * 60)
    print("Testing /options endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/options", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        options = data.get('options', [])
        count = data.get('count', len(options))
        print(f"✓ Options retrieved")
        print(f"  Total options: {count}")
        
        if options:
            # Show a sample option
            sample = options[0]
            print(f"\n  Sample option:")
            print(f"    Type: {sample.get('option_type')}")
            print(f"    Strike: ${sample.get('strike', 0):.2f}")
            print(f"    Price: ${sample.get('mid_px', 0):.4f}")
            print(f"    IV: {sample.get('implied_vol', 0):.4f}")
            print(f"    Delta: {sample.get('delta', 0):.4f}")
            print(f"    Expiry: {sample.get('expiry', 'N/A')}")
        
        return True
    except Exception as e:
        print(f"✗ Options retrieval failed: {e}")
        return False


def test_submit_order(side: str = "BUY", quantity: int = 100, price: float = 100.0, order_type: str = "LIMIT") -> Dict[str, Any]:
    """Test submitting an order."""
    print("\n" + "=" * 60)
    print(f"Testing /orders endpoint (POST)...")
    try:
        payload = {
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_type": order_type
        }
        response = requests.post(
            f"{BASE_URL}/orders",
            json=payload,
            timeout=TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        print(f"✓ Order submitted")
        print(f"  Order ID: {data.get('order_id')}")
        print(f"  Status: {data.get('status')}")
        print(f"  Side: {data.get('side')}")
        print(f"  Quantity: {data.get('quantity')}")
        print(f"  Price: ${data.get('price', 'N/A')}")
        print(f"  Filled: {data.get('filled_quantity', 0)}")
        return data
    except Exception as e:
        print(f"✗ Order submission failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_data = e.response.json()
                print(f"  Error details: {error_data}")
            except:
                print(f"  Error: {e.response.text}")
        return {}


def test_get_order(order_id: int) -> bool:
    """Test getting an order status."""
    print("\n" + "=" * 60)
    print(f"Testing /orders/{order_id} endpoint (GET)...")
    try:
        response = requests.get(f"{BASE_URL}/orders/{order_id}", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        print(f"✓ Order status retrieved")
        print(f"  Order ID: {data.get('order_id')}")
        print(f"  Status: {data.get('status')}")
        print(f"  Filled: {data.get('filled_quantity', 0)}/{data.get('quantity', 0)}")
        return True
    except Exception as e:
        print(f"✗ Order status retrieval failed: {e}")
        return False


def test_cancel_order(order_id: int) -> bool:
    """Test canceling an order."""
    print("\n" + "=" * 60)
    print(f"Testing /orders/{order_id} endpoint (DELETE)...")
    try:
        response = requests.delete(f"{BASE_URL}/orders/{order_id}", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        print(f"✓ Order cancelled")
        print(f"  Order ID: {data.get('order_id')}")
        print(f"  Status: {data.get('status')}")
        return True
    except Exception as e:
        print(f"✗ Order cancellation failed: {e}")
        return False


def test_market_order() -> bool:
    """Test submitting a market order."""
    print("\n" + "=" * 60)
    print("Testing market order submission...")
    try:
        # Get current quote to determine reasonable quantity
        quote_response = requests.get(f"{BASE_URL}/quote", timeout=TIMEOUT)
        quote = quote_response.json()
        mid_price = quote.get('mid', 100.0)
        
        payload = {
            "side": "BUY",
            "quantity": 50,
            "order_type": "MARKET"
        }
        response = requests.post(
            f"{BASE_URL}/orders",
            json=payload,
            timeout=TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        print(f"✓ Market order submitted")
        print(f"  Order ID: {data.get('order_id')}")
        print(f"  Status: {data.get('status')}")
        print(f"  Filled: {data.get('filled_quantity', 0)}/{data.get('quantity', 0)}")
        return True
    except Exception as e:
        print(f"✗ Market order submission failed: {e}")
        return False


async def test_websocket_async(duration: int = 5) -> Dict[str, Any]:
    """Test WebSocket connection and message reception."""
    if not WEBSOCKETS_AVAILABLE:
        return {"success": False, "reason": "websockets library not available"}
    
    print("\n" + "=" * 60)
    print(f"Testing WebSocket endpoint (listening for {duration} seconds)...")
    
    messages_received = {
        "quote": [],
        "trade": [],
        "options": []
    }
    
    try:
        async with websockets.connect(WS_URL) as websocket:
            print(f"✓ WebSocket connected to {WS_URL}")
            
            # Listen for messages
            start_time = time.time()
            message_count = 0
            
            while time.time() - start_time < duration:
                try:
                    # Set a timeout for receiving messages
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(message)
                    msg_type = data.get("type", "unknown")
                    
                    if msg_type in messages_received:
                        messages_received[msg_type].append(data.get("data", {}))
                        message_count += 1
                        
                        # Print first message of each type
                        if len(messages_received[msg_type]) == 1:
                            print(f"  Received {msg_type} message:")
                            if msg_type == "quote":
                                q = data.get("data", {})
                                print(f"    Bid: ${q.get('bid', 0):.2f}, Ask: ${q.get('ask', 0):.2f}, Mid: ${q.get('mid', 0):.2f}")
                            elif msg_type == "trade":
                                t = data.get("data", {})
                                print(f"    {t.get('side')} {t.get('quantity')} @ ${t.get('price', 0):.2f}")
                            elif msg_type == "options":
                                o = data.get("data", {})
                                print(f"    Options count: {o.get('count', 0)}")
                    
                except asyncio.TimeoutError:
                    # Continue listening
                    continue
                except Exception as e:
                    print(f"  Error receiving message: {e}")
                    break
            
            print(f"✓ WebSocket test completed")
            print(f"  Total messages received: {message_count}")
            print(f"  Quote messages: {len(messages_received['quote'])}")
            print(f"  Trade messages: {len(messages_received['trade'])}")
            print(f"  Options messages: {len(messages_received['options'])}")
            
            # Success if we received at least one message
            success = message_count > 0
            if not success:
                print("  ⚠ Warning: No messages received during test period")
            
            return {
                "success": success,
                "message_count": message_count,
                "messages_by_type": {k: len(v) for k, v in messages_received.items()}
            }
            
    except Exception as e:
        print(f"✗ WebSocket test failed: {e}")
        return {"success": False, "error": str(e)}


def test_websocket(duration: int = 5) -> bool:
    """Synchronous wrapper for WebSocket test."""
    if not WEBSOCKETS_AVAILABLE:
        print("\n" + "=" * 60)
        print("Skipping WebSocket test (websockets library not available)")
        return False
    
    try:
        result = asyncio.run(test_websocket_async(duration))
        return result.get("success", False)
    except Exception as e:
        print(f"✗ WebSocket test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Market Feed Service - Test Suite")
    print("=" * 60)
    print(f"\nConnecting to: {BASE_URL}")
    print("Waiting for service to initialize...")
    time.sleep(3)  # Give service time to start
    
    results = []
    
    # Basic endpoint tests
    results.append(("Health Check", test_health()))
    results.append(("Quote", test_quote()))
    results.append(("Orderbook", test_orderbook(depth=5)))
    results.append(("Options", test_options()))
    
    # Order tests
    order_result = test_submit_order(side="BUY", quantity=100, price=99.50, order_type="LIMIT")
    order_id = order_result.get('order_id') if order_result else None
    
    if order_id:
        results.append(("Get Order", test_get_order(order_id)))
        # Don't cancel immediately - let it sit for a moment
        time.sleep(1)
        results.append(("Cancel Order", test_cancel_order(order_id)))
    
    # Market order test
    results.append(("Market Order", test_market_order()))
    
    # WebSocket test
    results.append(("WebSocket", test_websocket(duration=5)))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit(main())
