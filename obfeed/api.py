"""REST API and WebSocket server for OBFeed."""

import asyncio
import json
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from obfeed.engine import MarketSimulator, TradeEvent, MarketTickEvent
from obfeed.config import EngineConfig, MarketSimConfig, OptionChainConfig
from obfeed.orderbook import OrderBookManager


app = FastAPI(title="OBFeed API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartRequest(BaseModel):
    """Request to start the feed."""
    config: Optional[Dict] = None


class StopRequest(BaseModel):
    """Request to stop the feed."""
    pass


class OrderRequest(BaseModel):
    """Request to submit an order."""
    side: str
    quantity: int
    price: Optional[float] = None
    order_type: str = "LIMIT"


class ConfigUpdateRequest(BaseModel):
    """Request to update configuration."""
    config: Dict


class FeedState(BaseModel):
    """Current feed state."""
    running: bool
    symbol: str
    venue: str
    current_price: float
    simulation_time: float
    step_count: int
    config: Dict


class OrderBookResponse(BaseModel):
    """Orderbook snapshot response."""
    bids: List[Dict]
    asks: List[Dict]
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float


class FeedManager:
    """Manages feed state and execution."""
    
    def __init__(self):
        self.config = EngineConfig()
        self.simulator: Optional[MarketSimulator] = None
        self.orderbook = OrderBookManager()
        self.running = False
        self.step_count = 0
        self.thread: Optional[threading.Thread] = None
        self.websocket_clients: Set[WebSocket] = set()
        self._lock = threading.Lock()
        self._message_queue: List[Dict] = []
        self._queue_lock = threading.Lock()
    
    def start(self, config: Optional[Dict] = None):
        """Start the feed."""
        with self._lock:
            if self.running:
                raise ValueError("Feed is already running")
            
            if config:
                if "symbol" in config:
                    self.config.symbol = config["symbol"]
                if "venue" in config:
                    self.config.venue = config["venue"]
                if "initial_price" in config:
                    self.config.initial_price = config["initial_price"]
                if "rng_seed" in config:
                    self.config.rng_seed = config["rng_seed"]
                if "market" in config:
                    market_cfg = MarketSimConfig(**config["market"])
                    self.config.market = market_cfg
            
            print(f"Starting feed with initial_price={self.config.initial_price}, symbol={self.config.symbol}", flush=True)
            self.simulator = MarketSimulator(
                initial_price=self.config.initial_price,
                cfg=self.config.market,
                rng_seed=self.config.rng_seed,
            )
            self.orderbook = OrderBookManager()
            self.running = True
            self.step_count = 0
            
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"Feed thread started (running={self.running})", flush=True)
    
    def stop(self):
        """Stop the feed."""
        with self._lock:
            self.running = False
            if self.thread:
                self.thread.join(timeout=2.0)
            self.simulator = None
    
    def _run_loop(self):
        """Main simulation loop."""
        import sys
        print(f"Feed simulation loop started (wall_clock_sleep={self.config.wall_clock_sleep})", flush=True)
        while self.running:
            try:
                if not self.simulator:
                    print("Error: Simulator is None")
                    break
                
                result = self.simulator.step()
                self.step_count += 1
                
                self._broadcast_update(result)
                
                if self.config.wall_clock_sleep > 0:
                    time.sleep(self.config.wall_clock_sleep)
                else:
                    time.sleep(0.01)
                    
            except Exception as e:
                print(f"Error in simulation loop: {e}")
                import traceback
                traceback.print_exc()
                break
        
        print("Feed simulation loop stopped")
        self.running = False
    
    def _broadcast_update(self, result: Dict):
        """Broadcast update to all WebSocket clients."""
        tick = result["tick"]
        trades = result["trades"]
        options = result.get("options")
        
        message = {
            "type": "update",
            "timestamp": datetime.now().isoformat(),
            "tick": {
                "ts": tick.ts,
                "mid": tick.mid,
                "bid": tick.bid,
                "ask": tick.ask,
                "bid_size": tick.bid_size,
                "ask_size": tick.ask_size,
            },
            "trades": [
                {
                    "ts": t.ts,
                    "price": t.price,
                    "qty": t.qty,
                    "side": t.side,
                }
                for t in trades
            ],
            "options": options or [],
        }
        
        with self._queue_lock:
            self._message_queue.append(message)
            if len(self._message_queue) > 100:
                self._message_queue.pop(0)
        
        if self.step_count % 10 == 0:
            print(f"Feed step {self.step_count}: Mid=${tick.mid:.2f}, Queue size={len(self._message_queue)}, Clients={len(self.websocket_clients)}")
    
    def get_state(self) -> FeedState:
        """Get current feed state."""
        current_price = 0.0
        sim_time = 0.0
        
        if self.simulator:
            current_price = self.simulator.current_price
            sim_time = self.simulator.t
        
        return FeedState(
            running=self.running,
            symbol=self.config.symbol,
            venue=self.config.venue,
            current_price=current_price,
            simulation_time=sim_time,
            step_count=self.step_count,
            config={
                "symbol": self.config.symbol,
                "venue": self.config.venue,
                "initial_price": self.config.initial_price,
                "wall_clock_sleep": self.config.wall_clock_sleep,
            },
        )


feed_manager = FeedManager()


@app.on_event("startup")
async def startup_event():
    """Auto-start the feed when the service starts."""
    import sys
    print("STARTUP EVENT: Starting feed...", flush=True)
    try:
        feed_manager.start()
        print("STARTUP EVENT: OBFeed service started - market feed is running", flush=True)
    except Exception as e:
        print(f"STARTUP EVENT ERROR: Failed to start feed: {e}", flush=True, file=sys.stderr)
        import traceback
        traceback.print_exc()


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "OBFeed",
        "version": "0.1.0",
        "status": "running" if feed_manager.running else "stopped",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/feed/start")
async def start_feed(request: StartRequest):
    """Start the market feed."""
    try:
        feed_manager.start(config=request.config)
        return {"status": "started", "message": "Feed started successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start feed: {str(e)}")


@app.post("/feed/stop")
async def stop_feed():
    """Stop the market feed."""
    feed_manager.stop()
    return {"status": "stopped", "message": "Feed stopped successfully"}


@app.get("/feed/state")
async def get_feed_state():
    """Get current feed state."""
    return feed_manager.get_state()


@app.get("/feed/quote")
async def get_current_quote():
    """Get current market quote."""
    try:
        if not feed_manager.running or not feed_manager.simulator:
            feed_manager.start()
        
        if not feed_manager.simulator:
            raise HTTPException(status_code=503, detail="Feed is initializing")
        
        price = feed_manager.simulator.current_price
        tick = feed_manager.simulator._quote_from_price(price)
        
        return {
            "ts": tick.ts,
            "mid": tick.mid,
            "bid": tick.bid,
            "ask": tick.ask,
            "bid_size": tick.bid_size,
            "ask_size": tick.ask_size,
        }
    except Exception as e:
        import sys
        print(f"Error in /feed/quote: {e}", flush=True, file=sys.stderr)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error getting quote: {str(e)}")


@app.get("/feed/options")
async def get_option_chain():
    """Get current option chain."""
    if not feed_manager.running or not feed_manager.simulator:
        feed_manager.start()
    
    if not feed_manager.simulator:
        raise HTTPException(status_code=503, detail="Feed is initializing")
    
    spot = feed_manager.simulator.current_price
    ts = feed_manager.simulator._iso_ts()
    options = feed_manager.simulator.option_chain.build_chain(spot, ts)
    
    return {"options": options or [], "underlying_price": spot}


@app.get("/orderbook")
async def get_orderbook(max_levels: int = 10):
    """Get orderbook snapshot."""
    snapshot = feed_manager.orderbook.get_snapshot(max_levels=max_levels)
    return OrderBookResponse(
        bids=snapshot.bids,
        asks=snapshot.asks,
        best_bid=snapshot.best_bid,
        best_ask=snapshot.best_ask,
        mid_price=snapshot.mid_price,
        spread=snapshot.spread,
    )


@app.post("/orderbook/order")
async def submit_order(request: OrderRequest):
    """Submit an order to the orderbook."""
    if not feed_manager.running:
        feed_manager.start()
    
    timestamp = time.time()
    
    if request.order_type == "MARKET":
        trades = feed_manager.orderbook.match_market_order(
            side=request.side,
            quantity=request.quantity,
            timestamp=timestamp,
        )
        return {
            "status": "filled",
            "order_type": "MARKET",
            "trades": trades,
        }
    elif request.order_type == "LIMIT":
        if request.price is None:
            raise HTTPException(status_code=400, detail="Price required for limit orders")
        
        order_id = feed_manager.orderbook.insert_limit_order(
            side=request.side,
            price=request.price,
            quantity=request.quantity,
            timestamp=timestamp,
        )
        return {
            "status": "submitted",
            "order_type": "LIMIT",
            "order_id": order_id,
        }
    else:
        raise HTTPException(status_code=400, detail=f"Unknown order type: {request.order_type}")


@app.post("/config")
async def update_config(request: ConfigUpdateRequest):
    """Update feed configuration."""
    if feed_manager.running:
        raise HTTPException(status_code=400, detail="Cannot update config while feed is running")
    
    feed_manager.start(config=request.config)
    return {"status": "updated", "message": "Configuration updated"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    import sys
    await websocket.accept()
    feed_manager.websocket_clients.add(websocket)
    print(f"WebSocket client connected. Feed running: {feed_manager.running}", flush=True)
    
    try:
        try:
            state = feed_manager.get_state()
            await websocket.send_json({
                "type": "state",
                "data": state.dict(),
            })
            print(f"Sent initial state to WebSocket client", flush=True)
        except Exception as e:
            print(f"Error sending initial state: {e}", flush=True, file=sys.stderr)
            import traceback
            traceback.print_exc()
        
        last_sent_timestamp = None
        with feed_manager._queue_lock:
            queue_size = len(feed_manager._message_queue)
            if queue_size > 0:
                start_idx = max(0, queue_size - 10)
                recent_messages = feed_manager._message_queue[start_idx:]
                for msg in recent_messages:
                    await websocket.send_json(msg)
                if recent_messages:
                    last_sent_timestamp = recent_messages[-1].get("timestamp")
        
        print(f"WebSocket client ready. Sent {len(recent_messages) if queue_size > 0 else 0} recent messages, Feed running: {feed_manager.running}", flush=True)
        
        while True:
            try:
                with feed_manager._queue_lock:
                    new_messages = []
                    for msg in feed_manager._message_queue:
                        msg_timestamp = msg.get("timestamp")
                        if last_sent_timestamp is None or msg_timestamp > last_sent_timestamp:
                            new_messages.append(msg)
                    
                    if new_messages:
                        for msg in new_messages:
                            await websocket.send_json(msg)
                        last_sent_timestamp = new_messages[-1].get("timestamp")
                
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                    await websocket.send_json({
                        "type": "echo",
                        "data": json.loads(data),
                    })
                except asyncio.TimeoutError:
                    continue
            except WebSocketDisconnect:
                print("WebSocket client disconnected", flush=True)
                break
    except Exception as e:
        print(f"WebSocket error: {e}", flush=True, file=sys.stderr)
        import traceback
        traceback.print_exc()
    finally:
        feed_manager.websocket_clients.discard(websocket)
        print("WebSocket client removed from clients set", flush=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
