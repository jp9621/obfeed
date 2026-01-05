"""Orderbook management wrapper."""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from ob import OrderBook, Order, OrderSide


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
    
    def __init__(self):
        """
        Initialize orderbook manager.
        
        Requires the C++ ob module to be available.
        """
        self.orderbook = OrderBook()
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
        order_side = OrderSide.Buy if side == "BUY" else OrderSide.Sell
        order = Order(order_id, price, quantity, order_side, timestamp)
        self.orderbook.insertOrder(order)
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
        order_side = OrderSide.Buy if side == "BUY" else OrderSide.Sell
        cpp_trades = self.orderbook.matchOrder(order_side, 0.0, quantity, timestamp)
        for trade in cpp_trades:
            trades.append({
                "price": trade.price,
                "quantity": trade.quantity,
                "timestamp": trade.timestamp,
            })
        return trades
    
    def get_snapshot(self, max_levels: int = 10) -> OrderBookSnapshot:
        """
        Get orderbook snapshot.
        
        Args:
            max_levels: Maximum number of levels to return per side
            
        Returns:
            OrderBookSnapshot
        """
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
