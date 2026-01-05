"""
FastAPI server for containerized market feed service.
Provides REST and WebSocket APIs for orderbook, quotes, trades, and options.
"""

import asyncio
import json
import math
import random
import threading
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from feed import MarketSimulator, MarketSimConfig
from hft import OrderBook, Order, OrderSide

app = FastAPI(title="Market Feed Service", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
orderbook: Optional[OrderBook] = None
simulator: Optional[MarketSimulator] = None
sim_thread: Optional[threading.Thread] = None
sim_running = False

# Recent data buffers
recent_quotes: deque = deque(maxlen=100)
recent_trades: deque = deque(maxlen=100)
recent_options: deque = deque(maxlen=1000)
current_quote: Optional[Dict] = None
current_options: Optional[List[Dict]] = None

# Order management
next_order_id = 1
active_orders: Dict[int, Dict] = {}


# Pydantic models
class OrderRequest(BaseModel):
    side: str  # "BUY" or "SELL"
    quantity: int
    price: Optional[float] = None  # None for market orders
    order_type: str = "LIMIT"  # "MARKET" or "LIMIT"


class OrderResponse(BaseModel):
    order_id: int
    status: str
    side: str
    quantity: int
    price: Optional[float]
    filled_quantity: int = 0
    timestamp: str


def init_orderbook():
    """Initialize the C++ orderbook."""
    global orderbook
    orderbook = OrderBook()
    print("Orderbook initialized")


def init_simulator():
    """Initialize the market simulator."""
    global simulator
    config = MarketSimConfig(
        dt=1.0,
        trade_intensity=1.0,
    )
    simulator = MarketSimulator(initial_price=100.0, cfg=config, rng_seed=42)
    print("Market simulator initialized")


def get_volume_to_price(ob: OrderBook, current: float, target: float) -> int:
    """Calculate volume needed to move from current to target price."""
    levels = ob.getSortedLevels()
    total = 0
    if target > current:
        # Need to consume asks to move price up
        for price, level in sorted(levels.items()):
            if current < price <= target:
                total += level.getAskQuantity()
    else:
        # Need to consume bids to move price down
        for price, level in sorted(levels.items(), reverse=True):
            if target <= price < current:
                total += level.getBidQuantity()
    return total


def generate_signed_splits(net_qty: int) -> List[tuple]:
    """Generate signed quantity splits for trades."""
    if net_qty == 0:
        return []
    
    n = random.randint(1, min(10, max(3, abs(net_qty) // 100)))
    splits = []
    current_position = 0
    
    for _ in range(n - 1):
        max_split = min(abs(net_qty) // 2, abs(net_qty) - abs(current_position))
        q = random.randint(max(1, max_split // 10), max(1, max_split // 2))
        
        if current_position < net_qty:
            s = 1
        else:
            s = -1
        
        splits.append((s, q))
        current_position += s * q
    
    remaining = net_qty - current_position
    if remaining != 0:
        splits.append((1 if remaining > 0 else -1, abs(remaining)))
    
    random.shuffle(splits)
    return splits


def generate_hawkes_times(N: int) -> List[float]:
    """Generate trade times using Hawkes process."""
    HAWKES_MU = 0.5
    HAWKES_ALPHA = 0.8
    HAWKES_BETA = 1.0
    
    times = []
    t = 0.0
    
    while len(times) < N:
        lam_up = HAWKES_MU + HAWKES_ALPHA * len(times)
        w = -math.log(random.random()) / lam_up
        t += w
        
        lam_act = HAWKES_MU + sum(
            HAWKES_ALPHA * math.exp(-HAWKES_BETA * (t - ti))
            for ti in times
        )
        
        if random.random() * lam_up <= lam_act:
            times.append(t)
    
    return times


def populate_orders_around_price(ob: OrderBook, price: float, timestamp: float, levels: int = 15, start_order_id: int = None):
    """Populate orderbook with limit orders around current price - matches old logic."""
    global next_order_id
    
    tick_size = 0.01
    base_qty = 500
    decay_rate = 0.1
    
    order_id = start_order_id if start_order_id is not None else next_order_id
    
    # Only add orders if orderbook is getting sparse
    existing_levels = len(ob.getSortedLevels())
    if existing_levels > levels * 2:
        return  # Orderbook already has enough liquidity
    
    for level in range(1, levels + 1):
        mean_qty = int(base_qty * math.exp(-decay_rate * level))
        buy_qty = max(1, int(np.random.exponential(scale=mean_qty)))
        sell_qty = max(1, int(np.random.exponential(scale=mean_qty)))
        
        buy_price = price - level * tick_size
        sell_price = price + level * tick_size
        
        # Check if level already exists and needs more liquidity
        buy_level = ob.getPriceLevel(buy_price)
        ask_level = ob.getPriceLevel(sell_price)
        
        if not buy_level or buy_level.getBidQuantity() < mean_qty // 2:
            ob.insertOrder(Order(order_id, buy_price, buy_qty, OrderSide.Buy, timestamp))
            order_id += 1
        
        if not ask_level or ask_level.getAskQuantity() < mean_qty // 2:
            ob.insertOrder(Order(order_id, sell_price, sell_qty, OrderSide.Sell, timestamp))
            order_id += 1
    
    if start_order_id is None:
        next_order_id = order_id


def process_user_orders():
    """Process pending user orders."""
    global active_orders, orderbook
    
    if not orderbook:
        return
    
    orders_to_remove = []
    for order_id, order_info in active_orders.items():
        if order_info["status"] == "FILLED":
            orders_to_remove.append(order_id)
            continue
        
        # For now, market orders execute immediately, limit orders wait for matching
        if order_info["order_type"] == "MARKET":
            side = OrderSide.Buy if order_info["side"] == "BUY" else OrderSide.Sell
            trades = orderbook.matchOrder(side, 0.0, order_info["quantity"], time.time())
            if trades:
                filled_qty = sum(t.quantity for t in trades)
                order_info["filled_quantity"] = filled_qty
                if filled_qty >= order_info["quantity"]:
                    order_info["status"] = "FILLED"
                else:
                    order_info["status"] = "PARTIALLY_FILLED"
    
    for order_id in orders_to_remove:
        del active_orders[order_id]


def run_simulation():
    """Run the market simulation in a background thread - matches old logic."""
    global sim_running, current_quote, current_options, recent_quotes, recent_trades, recent_options, orderbook, simulator, next_order_id

    sim_running = True
    init_orderbook()
    init_simulator()

    # Initialize with a starting price
    current_price = 100.0
    t = time.time()
    
    # Populate initial orderbook
    if orderbook:
        populate_orders_around_price(orderbook, current_price, t, start_order_id=1)

    while sim_running:
        try:
            if not orderbook or not simulator:
                break

            # Get current price from orderbook (source of truth)
            best_bid = orderbook.getBestBid()
            best_ask = orderbook.getBestAsk()
            
            if best_bid > 0 and best_ask > 0:
                current_price = (best_bid + best_ask) / 2.0
            else:
                # Fallback to simulator price if orderbook is empty
                current_price = simulator.current_price

            # Generate target price via jump diffusion
            target_price = simulator.jump_process.step(current_price)
            
            # Calculate volume needed to move from current to target
            raw_vol = get_volume_to_price(orderbook, current_price, target_price)
            
            # Generate signed splits and Hawkes times for trades
            net_qty = (1 if target_price > current_price else -1) * raw_vol
            splits = generate_signed_splits(net_qty)
            hawkes_times = generate_hawkes_times(len(splits))
            
            # Execute trades through orderbook
            all_trades = []
            base_time = time.time()
            
            for (sign, qty), dt_offset in zip(splits, hawkes_times):
                side = OrderSide.Buy if sign > 0 else OrderSide.Sell
                trade_time = base_time + dt_offset
                
                # Match order through orderbook
                trades = orderbook.matchOrder(side, 0.0, qty, trade_time)
                
                # Record trades
                for trade in trades:
                    all_trades.append({
                        "ts": datetime.fromtimestamp(trade.timestamp).isoformat(),
                        "price": trade.price,
                        "quantity": trade.quantity,
                        "side": "BUY" if trade.side == OrderSide.Buy else "SELL",
                    })
                    recent_trades.append({
                        "ts": datetime.fromtimestamp(trade.timestamp).isoformat(),
                        "price": trade.price,
                        "quantity": trade.quantity,
                        "side": "BUY" if trade.side == OrderSide.Buy else "SELL",
                    })
            
            # Update simulator's current price to match orderbook
            best_bid = orderbook.getBestBid()
            best_ask = orderbook.getBestAsk()
            
            if best_bid > 0 and best_ask > 0:
                new_price = (best_bid + best_ask) / 2.0
                simulator.current_price = new_price
                
                # Update quote from orderbook (source of truth)
                best_bid_level = orderbook.getPriceLevel(best_bid)
                best_ask_level = orderbook.getPriceLevel(best_ask)
                
                bid_size = best_bid_level.getBidQuantity() if best_bid_level else 0
                ask_size = best_ask_level.getAskQuantity() if best_ask_level else 0
                
                current_quote = {
                    "ts": datetime.now().isoformat(),
                    "bid": best_bid,
                    "ask": best_ask,
                    "mid": new_price,
                    "bid_size": bid_size,
                    "ask_size": ask_size,
                }
                recent_quotes.append(current_quote)
                
                # Update volatility and generate option chain using orderbook price
                simulator.option_chain.update_vol(price=new_price, dt=simulator.cfg.dt)
                options = simulator.option_chain.build_chain(spot=new_price, ts_iso=current_quote["ts"])
                
                if options:
                    current_options = options
                    recent_options.extend(options)
                
                # Populate orderbook around new price
                populate_orders_around_price(orderbook, new_price, time.time())
            else:
                # Fallback if orderbook is empty
                current_quote = {
                    "ts": datetime.now().isoformat(),
                    "bid": current_price - 0.05,
                    "ask": current_price + 0.05,
                    "mid": current_price,
                    "bid_size": 0,
                    "ask_size": 0,
                }

            # Process user orders
            process_user_orders()

            # Sleep to maintain simulation rate
            time.sleep(simulator.cfg.dt)

        except Exception as e:
            print(f"Simulation error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)


def populate_initial_orders(ob: OrderBook, price: float, timestamp: float, levels: int = 15):
    """Populate orderbook with initial limit orders."""
    populate_orders_around_price(ob, price, timestamp, levels, start_order_id=1)


def process_user_orders():
    """Process pending user orders."""
    global active_orders, orderbook
    
    if not orderbook:
        return
    
    orders_to_remove = []
    for order_id, order_info in active_orders.items():
        if order_info["status"] == "FILLED":
            orders_to_remove.append(order_id)
            continue
        
        # For now, market orders execute immediately, limit orders wait for matching
        if order_info["order_type"] == "MARKET":
            side = OrderSide.Buy if order_info["side"] == "BUY" else OrderSide.Sell
            trades = orderbook.matchOrder(side, 0.0, order_info["quantity"], time.time())
            if trades:
                filled_qty = sum(t.quantity for t in trades)
                order_info["filled_quantity"] = filled_qty
                if filled_qty >= order_info["quantity"]:
                    order_info["status"] = "FILLED"
                else:
                    order_info["status"] = "PARTIALLY_FILLED"
    
    for order_id in orders_to_remove:
        del active_orders[order_id]


@app.on_event("startup")
async def startup_event():
    """Start the simulation thread on server startup."""
    global sim_thread
    sim_thread = threading.Thread(target=run_simulation, daemon=True)
    sim_thread.start()
    print("Market feed service started")


@app.on_event("shutdown")
async def shutdown_event():
    """Stop the simulation on server shutdown."""
    global sim_running
    sim_running = False
    print("Market feed service stopped")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "orderbook_initialized": orderbook is not None,
        "simulator_running": sim_running,
    }


@app.get("/quote")
async def get_quote():
    """Get current market quote from orderbook."""
    if not orderbook:
        raise HTTPException(status_code=503, detail="Orderbook not initialized")
    
    best_bid = orderbook.getBestBid()
    best_ask = orderbook.getBestAsk()
    
    if best_bid <= 0 or best_ask <= 0:
        raise HTTPException(status_code=503, detail="Quote not available yet")
    
    best_bid_level = orderbook.getPriceLevel(best_bid)
    best_ask_level = orderbook.getPriceLevel(best_ask)
    
    bid_size = best_bid_level.getBidQuantity() if best_bid_level else 0
    ask_size = best_ask_level.getAskQuantity() if best_ask_level else 0
    
    return {
        "ts": datetime.now().isoformat(),
        "bid": best_bid,
        "ask": best_ask,
        "mid": (best_bid + best_ask) / 2.0,
        "bid_size": bid_size,
        "ask_size": ask_size,
    }


@app.get("/orderbook")
async def get_orderbook(depth: int = 10):
    """Get orderbook snapshot."""
    if not orderbook:
        raise HTTPException(status_code=503, detail="Orderbook not initialized")
    
    levels = orderbook.getSortedLevels()
    bids = []
    asks = []
    
    for price, level in levels.items():
        bid_qty = level.getBidQuantity()
        ask_qty = level.getAskQuantity()
        
        if bid_qty > 0:
            bids.append({"price": price, "quantity": bid_qty})
        if ask_qty > 0:
            asks.append({"price": price, "quantity": ask_qty})
    
    bids = sorted(bids, key=lambda x: -x["price"])[:depth]
    asks = sorted(asks, key=lambda x: x["price"])[:depth]
    
    best_bid = orderbook.getBestBid()
    best_ask = orderbook.getBestAsk()
    mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
    
    return {
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": best_ask - best_bid if best_bid > 0 and best_ask > 0 else 0.0,
    }


@app.get("/options")
async def get_options():
    """Get current option chain."""
    if not current_options:
        raise HTTPException(status_code=503, detail="Option chain not available yet")
    return {"options": current_options, "count": len(current_options)}


@app.post("/orders", response_model=OrderResponse)
async def submit_order(order: OrderRequest):
    """Submit an order."""
    global next_order_id, active_orders, orderbook
    
    if not orderbook:
        raise HTTPException(status_code=503, detail="Orderbook not initialized")
    
    if order.side not in ["BUY", "SELL"]:
        raise HTTPException(status_code=400, detail="Side must be BUY or SELL")
    
    if order.order_type == "LIMIT" and order.price is None:
        raise HTTPException(status_code=400, detail="Limit orders require a price")
    
    order_id = next_order_id
    next_order_id += 1
    
    order_side = OrderSide.Buy if order.side == "BUY" else OrderSide.Sell
    
    if order.order_type == "MARKET":
        # Market order - execute immediately
        trades = orderbook.matchOrder(order_side, 0.0, order.quantity, time.time())
        filled_qty = sum(t.quantity for t in trades) if trades else 0
        
        order_data = {
            "order_id": order_id,
            "status": "FILLED" if filled_qty >= order.quantity else "PARTIALLY_FILLED",
            "side": order.side,
            "quantity": order.quantity,
            "price": None,
            "filled_quantity": filled_qty,
            "timestamp": datetime.now().isoformat(),
        }
    else:
        # Limit order - add to orderbook
        if order.price is None:
            raise HTTPException(status_code=400, detail="Limit orders require a price")
        
        limit_order = Order(order_id, order.price, order.quantity, order_side, time.time())
        orderbook.insertOrder(limit_order)
        
        # Check if it matches immediately
        best_bid = orderbook.getBestBid()
        best_ask = orderbook.getBestAsk()
        immediate_match = (
            (order_side == OrderSide.Buy and order.price >= best_ask) or
            (order_side == OrderSide.Sell and order.price <= best_bid)
        )
        
        order_data = {
            "order_id": order_id,
            "status": "FILLED" if immediate_match else "PENDING",
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "filled_quantity": 0,
            "timestamp": datetime.now().isoformat(),
        }
        
        active_orders[order_id] = {
            "status": order_data["status"],
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "order_type": order.order_type,
        }
    
    return OrderResponse(**order_data)


@app.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(order_id: int):
    """Get order status."""
    if order_id in active_orders:
        order_info = active_orders[order_id]
        return OrderResponse(
            order_id=order_id,
            status=order_info["status"],
            side=order_info["side"],
            quantity=order_info["quantity"],
            price=order_info.get("price"),
            filled_quantity=order_info.get("filled_quantity", 0),
            timestamp=datetime.now().isoformat(),
        )
    raise HTTPException(status_code=404, detail="Order not found")


@app.delete("/orders/{order_id}")
async def cancel_order(order_id: int):
    """Cancel an order."""
    global active_orders, orderbook
    
    if order_id not in active_orders:
        raise HTTPException(status_code=404, detail="Order not found")
    
    order_info = active_orders[order_id]
    if order_info["status"] in ["FILLED", "CANCELLED"]:
        raise HTTPException(status_code=400, detail=f"Order already {order_info['status'].lower()}")
    
    # Cancel in orderbook
    if orderbook and order_info.get("price"):
        orderbook.cancelOrder(order_id, order_info["price"])
    
    order_info["status"] = "CANCELLED"
    return {"order_id": order_id, "status": "CANCELLED"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time market data."""
    await websocket.accept()
    
    try:
        while True:
            # Send current quote
            if current_quote:
                await websocket.send_json({
                    "type": "quote",
                    "data": current_quote,
                })
            
            # Send recent trades
            if recent_trades:
                for trade in list(recent_trades)[-5:]:  # Last 5 trades
                    await websocket.send_json({
                        "type": "trade",
                        "data": trade,
                    })
            
            # Send option chain updates (less frequently)
            if current_options and len(current_options) > 0:
                await websocket.send_json({
                    "type": "options",
                    "data": {
                        "count": len(current_options),
                        "sample": current_options[:10],  # First 10 options
                    },
                })
            
            await asyncio.sleep(1.0)  # Update every second
            
    except WebSocketDisconnect:
        print("WebSocket client disconnected")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
