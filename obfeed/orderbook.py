"""Orderbook management wrapper."""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass

try:
    from ob import OrderBook, Order, OrderSide
    HAS_OB = True
except ImportError:
    HAS_OB = False
    OrderBook = None
    Order = None
    OrderSide = None


@dataclass
class OrderBookSnapshot:
    """Snapshot of orderbook state."""
    bids: List[Dict[str, Any]]
    asks: List[Dict[str, Any]]
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float


class OrderBookManager:
    """Manages orderbook state and operations."""
    
    def __init__(self, use_cpp: bool = True):
        """
        Initialize orderbook manager.
        
        Args:
            use_cpp: If True, use C++ orderbook (requires ob module). 
                     If False or C++ not available, use Python-only implementation.
        """
        self.use_cpp = use_cpp and HAS_OB
        if self.use_cpp:
            self.orderbook = OrderBook()
        else:
            # Simple Python-only orderbook implementation
            self._bids: Dict[float, int] = {}  # price -> quantity
            self._asks: Dict[float, int] = {}  # price -> quantity
        
        self._next_order_id = 1
    
    def get_next_order_id(self) -> int:
        """Get next order ID."""
        order_id = self._next_order_id
        self._next_order_id += 1
        return order_id
    
    def insert_limit_order(self, side: str, price: float, quantity: int, timestamp: float) -> int:
        """
        Insert a limit order.
        
        Args:
            side: "BUY" or "SELL"
            price: Limit price
            quantity: Order quantity
            timestamp: Order timestamp
            
        Returns:
            Order ID
        """
        order_id = self.get_next_order_id()
        
        if self.use_cpp:
            order_side = OrderSide.Buy if side == "BUY" else OrderSide.Sell
            order = Order(order_id, price, quantity, order_side, timestamp)
            self.orderbook.insertOrder(order)
        else:
            # Python-only implementation
            if side == "BUY":
                self._bids[price] = self._bids.get(price, 0) + quantity
            else:
                self._asks[price] = self._asks.get(price, 0) + quantity
        
        return order_id
    
    def match_market_order(self, side: str, quantity: int, timestamp: float) -> List[Dict[str, Any]]:
        """
        Match a market order.
        
        Args:
            side: "BUY" or "SELL"
            quantity: Order quantity
            timestamp: Order timestamp
            
        Returns:
            List of trades executed
        """
        trades = []
        
        if self.use_cpp:
            order_side = OrderSide.Buy if side == "BUY" else OrderSide.Sell
            cpp_trades = self.orderbook.matchOrder(order_side, 0.0, quantity, timestamp)
            for trade in cpp_trades:
                trades.append({
                    "price": trade.price,
                    "quantity": trade.quantity,
                    "timestamp": trade.timestamp,
                })
        else:
            # Python-only implementation - simple matching
            if side == "BUY":
                # Buy market order - match against asks
                remaining = quantity
                sorted_asks = sorted(self._asks.items())
                for ask_price, ask_qty in sorted_asks:
                    if remaining <= 0:
                        break
                    fill_qty = min(remaining, ask_qty)
                    trades.append({
                        "price": ask_price,
                        "quantity": fill_qty,
                        "timestamp": timestamp,
                    })
                    remaining -= fill_qty
                    self._asks[ask_price] -= fill_qty
                    if self._asks[ask_price] <= 0:
                        del self._asks[ask_price]
            else:
                # Sell market order - match against bids
                remaining = quantity
                sorted_bids = sorted(self._bids.items(), reverse=True)
                for bid_price, bid_qty in sorted_bids:
                    if remaining <= 0:
                        break
                    fill_qty = min(remaining, bid_qty)
                    trades.append({
                        "price": bid_price,
                        "quantity": fill_qty,
                        "timestamp": timestamp,
                    })
                    remaining -= fill_qty
                    self._bids[bid_price] -= bid_qty
                    if self._bids[bid_price] <= 0:
                        del self._bids[bid_price]
        
        return trades
    
    def get_snapshot(self, max_levels: int = 10) -> OrderBookSnapshot:
        """
        Get orderbook snapshot.
        
        Args:
            max_levels: Maximum number of levels to return per side
            
        Returns:
            OrderBookSnapshot
        """
        if self.use_cpp:
            levels = self.orderbook.getSortedLevels()
            bids = []
            asks = []
            
            for price, level in levels.items():
                bid_qty = level.getBidQuantity()
                ask_qty = level.getAskQuantity()
                
                if bid_qty > 0:
                    bids.append({"price": price, "quantity": bid_qty})
                if ask_qty > 0:
                    asks.append({"price": price, "quantity": ask_qty})
            
            bids = sorted(bids, key=lambda x: -x["price"])[:max_levels]
            asks = sorted(asks, key=lambda x: x["price"])[:max_levels]
            
            best_bid = self.orderbook.getBestBid()
            best_ask = self.orderbook.getBestAsk()
        else:
            # Python-only implementation
            bids = [{"price": p, "quantity": q} for p, q in sorted(self._bids.items(), reverse=True)[:max_levels]]
            asks = [{"price": p, "quantity": q} for p, q in sorted(self._asks.items())[:max_levels]]
            
            best_bid = max(self._bids.keys()) if self._bids else 0.0
            best_ask = min(self._asks.keys()) if self._asks else 0.0
        
        mid_price = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
        spread = best_ask - best_bid if best_bid > 0 and best_ask > 0 else 0.0
        
        return OrderBookSnapshot(
            bids=bids,
            asks=asks,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            spread=spread,
        )
