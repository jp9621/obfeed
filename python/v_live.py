import numpy as np
import random
import math
import threading
import time
from collections import deque
import pandas as pd
import dash
from dash import html, dcc, Input, Output, State
import plotly.graph_objs as go
from hft import OrderBook, Order, OrderSide
import logging

MU, SIGMA = 0.0, 0.01
LAMBDA, JUMP_MU, JUMP_SIGMA = 0.1, 0.0, 0.02
DT = 1.0

HAWKES_MU, HAWKES_ALPHA, HAWKES_BETA = 0.5, 0.8, 1.0

LIMIT_BASE_QTY, DECAY_RATE = 500, 0.1
TICK_SIZE, NUM_LEVELS = 0.01, 100

_order_id = 0
pending_orders = []
orderbook = None
agg = None
recent_trades = deque(maxlen=10)
user_trades = deque(maxlen=10)
order_status_message = ""

class DataAggregator:
    def __init__(self, bar_interval=1.0):
        self.bar_interval = bar_interval
        self.bars = []
        self.current = None
        self.next_bar_time = time.time()

    def start_new_bar(self, price, timestamp):
        if self.current is not None:
            self.bars.append(self.current | {'time': self.next_bar_time})
        
        self.current = {
            'open': price,
            'high': price,
            'low': price,
            'close': price,
            'volume': 0
        }
        self.next_bar_time = timestamp + self.bar_interval

    def update(self, price, quantity, timestamp):
        if self.current is None:
            self.start_new_bar(price, timestamp)
        
        self.current['high'] = max(self.current['high'], price)
        self.current['low'] = min(self.current['low'], price)
        self.current['close'] = price
        self.current['volume'] += quantity

    def to_dataframe(self):
        all_bars = self.bars.copy()
        if self.current is not None:
            all_bars.append(self.current | {'time': self.next_bar_time})
        return pd.DataFrame(all_bars).set_index('time') if all_bars else pd.DataFrame()

class UserOrderManager:
    def __init__(self):
        self.next_order_id = 0
        self.active_orders = {}
        
    def get_next_order_id(self):
        self.next_order_id += 1
        return self.next_order_id

    def submit_market_order(self, side: str, quantity: int, timestamp: float):
        """Submit a market order to active orders."""
        side_enum = OrderSide.Buy if side == 'Buy' else OrderSide.Sell
        order_id = self.get_next_order_id()
        
        self.active_orders[order_id] = {
            'type': 'market',
            'side': side_enum,
            'total_quantity': quantity,
            'remaining_quantity': quantity,
            'child_order_id': None,
            'child_quantity': None,
            'timestamp': timestamp
        }
        return order_id

    def submit_limit_order(self, side: str, quantity: int, price: float, timestamp: float):
        """Submit a limit order to active orders."""
        side_enum = OrderSide.Buy if side == 'Buy' else OrderSide.Sell
        order_id = self.get_next_order_id()
        
        self.active_orders[order_id] = {
            'type': 'limit',
            'side': side_enum,
            'price': price,
            'total_quantity': quantity,
            'remaining_quantity': quantity,
            'child_order_id': None,
            'child_quantity': None,
            'timestamp': timestamp
        }
        return order_id

    def submit_iceberg_order(self, side: str, total_quantity: int, price: float, display_size: int, timestamp: float):
        """Submit an iceberg order to active orders."""
        side_enum = OrderSide.Buy if side == 'Buy' else OrderSide.Sell
        order_id = self.get_next_order_id()
        
        self.active_orders[order_id] = {
            'type': 'iceberg',
            'side': side_enum,
            'price': price,
            'total_quantity': total_quantity,
            'remaining_quantity': total_quantity,
            'display_size': display_size,
            'child_order_id': None,
            'child_quantity': None,
            'timestamp': timestamp
        }
        return order_id

    def submit_twap_order(self, side: str, quantity: int, duration: float, num_slices: int, timestamp: float):
        """Submit a TWAP order to active orders."""
        side_enum = OrderSide.Buy if side == 'Buy' else OrderSide.Sell
        order_id = self.get_next_order_id()
        
        slice_size = quantity // num_slices
        interval = duration / num_slices
        
        self.active_orders[order_id] = {
            'type': 'twap',
            'side': side_enum,
            'total_quantity': quantity,
            'remaining_quantity': quantity,
            'slice_size': slice_size,
            'interval': interval,
            'last_slice_time': timestamp,
            'end_time': timestamp + duration,
            'child_order_id': None,
            'child_quantity': None,
            'timestamp': timestamp
        }
        return order_id

    def submit_trickle_order(self, side: str, quantity: int, max_slice: int, min_slice: int, 
                           max_pause: float, min_pause: float, timestamp: float):
        """Submit a trickle order to active orders."""
        side_enum = OrderSide.Buy if side == 'Buy' else OrderSide.Sell
        order_id = self.get_next_order_id()
        
        self.active_orders[order_id] = {
            'type': 'trickle',
            'side': side_enum,
            'total_quantity': quantity,
            'remaining_quantity': quantity,
            'max_slice': max_slice,
            'min_slice': min_slice,
            'max_pause': max_pause,
            'min_pause': min_pause,
            'last_slice_time': timestamp,
            'child_order_id': None,
            'child_quantity': None,
            'timestamp': timestamp
        }
        return order_id

    def pull_orders(self, orderbook: OrderBook, timestamp: float) -> list:
        """Pull orders that should be executed in this cycle."""
        orders_to_execute = []
        
        for order_id, order_info in list(self.active_orders.items()):
            if order_info['remaining_quantity'] <= 0:
                del self.active_orders[order_id]
                continue
                
            child_id = order_info['child_order_id']
            child_exists = False
            if child_id is not None:
                for level in orderbook.getSortedLevels().values():
                    orders = level.getOrders(order_info['side'])
                    if any(order.getId() == child_id for order in orders):
                        child_exists = True
                        break
                
                if not child_exists:
                    order_info['remaining_quantity'] -= order_info['child_quantity']
                    order_info['child_order_id'] = None
                    order_info['child_quantity'] = None
                    
                    if order_info['remaining_quantity'] <= 0:
                        del self.active_orders[order_id]
                        continue
            
            if child_exists:
                continue
                
            if order_info['type'] == 'market':
                new_order = Order(
                    self.get_next_order_id(),
                    0.0,
                    order_info['remaining_quantity'],
                    order_info['side'],
                    timestamp
                )
                orders_to_execute.append(new_order)
                del self.active_orders[order_id]
                
            elif order_info['type'] == 'limit':
                new_order = Order(
                    self.get_next_order_id(),
                    order_info['price'],
                    order_info['remaining_quantity'],
                    order_info['side'],
                    timestamp
                )
                orders_to_execute.append(new_order)
                del self.active_orders[order_id]
                
            elif order_info['type'] == 'iceberg':
                slice_size = min(order_info['display_size'], order_info['remaining_quantity'])
                child_id = self.get_next_order_id()
                new_order = Order(
                    child_id,
                    order_info['price'],
                    slice_size,
                    order_info['side'],
                    timestamp
                )
                order_info['child_order_id'] = child_id
                order_info['child_quantity'] = slice_size
                orders_to_execute.append(new_order)
                user_trades.appendleft({
                    'side': 'Buy' if order_info['side'] == OrderSide.Buy else 'Sell',
                    'price': order_info['price'],
                    'quantity': slice_size,
                    'timestamp': timestamp,
                    'type': 'limit'
                })
                
            elif order_info['type'] == 'twap':
                if timestamp >= order_info['last_slice_time'] + order_info['interval']:
                    slice_size = min(order_info['slice_size'], order_info['remaining_quantity'])
                    child_id = self.get_next_order_id()
                    new_order = Order(
                        child_id,
                        0.0,
                        slice_size,
                        order_info['side'],
                        timestamp
                    )
                    order_info['child_order_id'] = child_id
                    order_info['child_quantity'] = slice_size
                    order_info['last_slice_time'] = timestamp
                    orders_to_execute.append(new_order)
                
            elif order_info['type'] == 'trickle':
                if timestamp >= order_info['last_slice_time'] + random.uniform(
                    order_info['min_pause'], order_info['max_pause']):
                    slice_size = random.randint(
                        order_info['min_slice'],
                        min(order_info['max_slice'], order_info['remaining_quantity'])
                    )
                    child_id = self.get_next_order_id()
                    new_order = Order(
                        child_id,
                        0.0,
                        slice_size,
                        order_info['side'],
                        timestamp
                    )
                    order_info['child_order_id'] = child_id
                    order_info['child_quantity'] = slice_size
                    order_info['last_slice_time'] = timestamp
                    orders_to_execute.append(new_order)
        
        return orders_to_execute

def next_order_id():
    global _order_id
    _order_id += 1
    return _order_id

def simulate_jump_diffusion(price):
    z = np.random.normal()
    drift = (MU - 0.5*SIGMA**2)*DT
    diffusion = SIGMA*math.sqrt(DT)*z
    n_jumps = np.random.poisson(LAMBDA*DT)
    jump_sum = sum(np.random.normal(JUMP_MU, JUMP_SIGMA) for _ in range(n_jumps))
    return price * math.exp(drift + diffusion + jump_sum)

def get_volume_to_price(ob, cur, tgt):
    levels = ob.getSortedLevels().items()
    total = 0
    if tgt > cur:
        for price, lvl in sorted(levels):
            if cur < price <= tgt:
                total += lvl.getAskQuantity()
    else:
        for price, lvl in sorted(levels, reverse=True):
            if tgt <= price < cur:
                total += lvl.getBidQuantity()
    return total

def generate_signed_splits(net_qty):
    if net_qty == 0:
        return []
    
    position_limit = int(abs(net_qty) * 1.2)
    
    n = min(10, max(3, abs(net_qty) // 100))
    
    splits = []
    current_position = 0
    
    for _ in range(n-1):
        max_split = min(
            position_limit - abs(current_position),
            abs(net_qty) // 2
        )
        
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

def generate_hawkes_times(N):
    times, t_ = [], 0.0
    while len(times) < N:
        lam_up = HAWKES_MU + HAWKES_ALPHA*len(times)
        w = -math.log(random.random())/lam_up
        t_ += w
        lam_act = HAWKES_MU + sum(
            HAWKES_ALPHA*math.exp(-HAWKES_BETA*(t_-ti))
            for ti in times
        )
        if random.random()*lam_up <= lam_act:
            times.append(t_)
    return times

def populate_limit_orders(ob, current_price, now):
    levels = 15
    for lvl in range(1, levels + 1):
        mean_qty = LIMIT_BASE_QTY * math.exp(-DECAY_RATE * lvl)
        buy_qty  = max(1, int(np.random.exponential(scale=mean_qty)))
        sell_qty = max(1, int(np.random.exponential(scale=mean_qty)))
        
        buy_price  = current_price - lvl * TICK_SIZE
        sell_price = current_price + lvl * TICK_SIZE

        ob.insertOrder(Order(next_order_id(), buy_price,  buy_qty,  OrderSide.Buy,  now))
        ob.insertOrder(Order(next_order_id(), sell_price, sell_qty, OrderSide.Sell, now))

def get_orderbook_table_snapshot(orderbook):
    levels = orderbook.getSortedLevels()
    rows = []
    best_bid = orderbook.getBestBid()
    best_ask = orderbook.getBestAsk()

    for price, level in levels.items():
        bid_qty = level.getBidQuantity()
        ask_qty = level.getAskQuantity()
        
        if bid_qty > 0:
            rows.append({
                'price': price,
                'quantity': bid_qty,
                'side': 'Bid'
            })
            
        if ask_qty > 0:
            rows.append({
                'price': price,
                'quantity': ask_qty,
                'side': 'Ask'
            })

    sorted_rows = sorted(rows, key=lambda x: -x['price'])
    bids = [row for row in sorted_rows if row['side'] == 'Bid']
    asks = [row for row in sorted_rows if row['side'] == 'Ask']
    
    top_bids = bids[:5]
    top_asks = asks[-5:]
    top_asks.reverse()
    
    return top_bids + top_asks

def format_price(price):
    return f"${price:.2f}"

app = dash.Dash(__name__, suppress_callback_exceptions=True)
order_manager = UserOrderManager()

app.layout = html.Div([
    html.H2("HFT Market Simulator", style={'textAlign': 'center', 'marginBottom': '20px'}),
    html.Div([
        html.Div([
            html.Div([
                html.Div([
                    html.Label("Order Type", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Dropdown(
                        ['Market', 'Limit', 'Iceberg', 'TWAP', 'Trickle'],
                        'Market',
                        id='order-type',
                        style={'width': '150px'}
                    ),
                ], style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Side", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Dropdown(
                        ['Buy', 'Sell'], 
                        'Buy', 
                        id='order-side',
                        style={'width': '100px'}
                    ),
                ], style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Quantity", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='order-qty',
                        type='number',
                        value=10,
                        min=1,
                        style={'width': '100px'}
                    ),
                ], style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Price", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='order-price',
                        type='number',
                        value=100.0,
                        step=0.01,
                        style={'width': '100px'}
                    ),
                ], id='price-input', style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Display Size", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='display-size',
                        type='number',
                        value=1,
                        min=1,
                        style={'width': '100px'}
                    ),
                ], id='display-size-input', style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Duration (s)", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='twap-duration',
                        type='number',
                        value=60,
                        min=1,
                        style={'width': '100px'}
                    ),
                ], id='twap-duration-input', style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Slices", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='twap-slices',
                        type='number',
                        value=10,
                        min=2,
                        style={'width': '100px'}
                    ),
                ], id='twap-slices-input', style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Max Slice", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='trickle-max-slice',
                        type='number',
                        value=5,
                        min=1,
                        style={'width': '100px'}
                    ),
                ], id='trickle-max-slice-input', style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Min Slice", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='trickle-min-slice',
                        type='number',
                        value=1,
                        min=1,
                        style={'width': '100px'}
                    ),
                ], id='trickle-min-slice-input', style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Max Pause (s)", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='trickle-max-pause',
                        type='number',
                        value=5.0,
                        min=0.1,
                        step=0.1,
                        style={'width': '100px'}
                    ),
                ], id='trickle-max-pause-input', style={'marginRight': '10px'}),
                
                html.Div([
                    html.Label("Min Pause (s)", style={'display': 'block', 'marginBottom': '5px'}),
                    dcc.Input(
                        id='trickle-min-pause',
                        type='number',
                        value=1.0,
                        min=0.1,
                        step=0.1,
                        style={'width': '100px'}
                    ),
                ], id='trickle-min-pause-input'),
            ], style={'display': 'flex', 'marginBottom': '20px', 'alignItems': 'flex-start'}),
            
            html.Button("Place Order", id='submit-order', n_clicks=0,
                      style={'backgroundColor': '#4CAF50', 'color': 'white', 'border': 'none', 'padding': '10px', 'cursor': 'pointer', 'width': '100%', 'marginBottom': '20px'}),
            
            html.Div(id='order-status', style={
                'marginBottom': '20px',
                'padding': '10px',
                'borderRadius': '5px',
                'textAlign': 'center',
                'fontWeight': 'bold'
            }),
            
            html.Div([
                html.Div([
                    html.H3("Order Book", style={'marginBottom': '10px', 'textAlign': 'center'}),
                    html.Div(id='orderbook-content', style={
                        'fontFamily': 'monospace',
                        'whiteSpace': 'pre-wrap',
                        'border': '1px solid #ddd',
                        'padding': '10px',
                        'borderRadius': '5px',
                        'backgroundColor': 'white'
                    })
                ], style={'flex': '1', 'marginRight': '20px'}),
                
                html.Div([
                    html.H3("Market Trades", style={'marginBottom': '10px', 'textAlign': 'center'}),
                    html.Div(id='market-trade-tape', style={
                        'fontFamily': 'monospace',
                        'whiteSpace': 'pre-wrap',
                        'border': '1px solid #ddd',
                        'padding': '10px',
                        'borderRadius': '5px',
                        'backgroundColor': 'white'
                    })
                ], style={'flex': '1', 'marginRight': '20px'}),
                
                html.Div([
                    html.H3("Your Trades", style={'marginBottom': '10px', 'textAlign': 'center'}),
                    html.Div(id='user-trade-tape', style={
                        'fontFamily': 'monospace',
                        'whiteSpace': 'pre-wrap',
                        'border': '1px solid #ddd',
                        'padding': '10px',
                        'borderRadius': '5px',
                        'backgroundColor': 'white'
                    })
                ], style={'flex': '1'})
            ], style={'display': 'flex', 'marginBottom': '20px'}),
            
            dcc.Graph(id='live-candles'),
        ]),
        dcc.Interval(id='update-interval', interval=1000, n_intervals=0),
    ], style={'maxWidth': '1200px', 'margin': '0 auto', 'padding': '20px'}),
])

@app.callback(
    [Output('price-input', 'style'),
     Output('display-size-input', 'style'),
     Output('twap-duration-input', 'style'),
     Output('twap-slices-input', 'style'),
     Output('trickle-max-slice-input', 'style'),
     Output('trickle-min-slice-input', 'style'),
     Output('trickle-max-pause-input', 'style'),
     Output('trickle-min-pause-input', 'style')],
    Input('order-type', 'value')
)
def update_params_visibility(order_type):
    base_style = {'marginRight': '10px'}
    hidden_style = {'marginRight': '10px', 'display': 'none'}
    
    price_style = base_style if order_type in ['Limit', 'Iceberg'] else hidden_style
    
    display_style = base_style if order_type == 'Iceberg' else hidden_style
    
    twap_duration_style = base_style if order_type == 'TWAP' else hidden_style
    twap_slices_style = base_style if order_type == 'TWAP' else hidden_style
    
    trickle_style = base_style if order_type == 'Trickle' else hidden_style
    
    return (
        price_style,
        display_style,
        twap_duration_style,
        twap_slices_style,
        trickle_style,
        trickle_style,
        trickle_style,
        trickle_style
    )

@app.callback(
    [Output('submit-order', 'n_clicks'),
     Output('order-side', 'value'),
     Output('order-qty', 'value'),
     Output('order-status', 'children'),
     Output('order-status', 'style')],
    [Input('submit-order', 'n_clicks'),
     State('order-type', 'value'),
     State('order-side', 'value'),
     State('order-qty', 'value'),
     State('order-price', 'value'),
     State('display-size', 'value'),
     State('twap-duration', 'value'),
     State('twap-slices', 'value'),
     State('trickle-max-slice', 'value'),
     State('trickle-min-slice', 'value'),
     State('trickle-max-pause', 'value'),
     State('trickle-min-pause', 'value')]
)
def place_order(n_clicks, order_type, side, qty, price, display_size, 
                twap_duration, twap_slices, trickle_max_slice, trickle_min_slice,
                trickle_max_pause, trickle_min_pause):
    base_status_style = {
        'marginTop': '10px',
        'padding': '10px',
        'borderRadius': '5px',
        'textAlign': 'center',
        'fontWeight': 'bold'
    }
    
    if n_clicks is None or n_clicks == 0:
        return 0, 'Buy', 10, '', {**base_status_style, 'display': 'none'}
        
    try:
        if side is None or qty is None or qty <= 0:
            return n_clicks, side, qty, "Error: Invalid side or quantity", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            
        now = time.time()
        order_id = None

        if order_type == 'Market':
            order_id = order_manager.submit_market_order(side, int(qty), now)
            
        elif order_type == 'Limit':
            if price is None or price <= 0:
                return n_clicks, side, qty, "Error: Invalid price for limit order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            order_id = order_manager.submit_limit_order(side, int(qty), float(price), now)
            user_trades.appendleft({
                'side': side,
                'price': float(price),
                'quantity': int(qty),
                'timestamp': now,
                'type': 'limit'
            })
            
        elif order_type == 'Iceberg':
            if price is None or price <= 0:
                return n_clicks, side, qty, "Error: Invalid price for iceberg order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            if display_size is None or display_size <= 0 or display_size > qty:
                return n_clicks, side, qty, "Error: Invalid display size for iceberg order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            order_id = order_manager.submit_iceberg_order(side, int(qty), float(price), int(display_size), now)
            
        elif order_type == 'TWAP':
            if twap_duration is None or twap_duration <= 0:
                return n_clicks, side, qty, "Error: Invalid duration for TWAP order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            if twap_slices is None or twap_slices <= 1:
                return n_clicks, side, qty, "Error: Invalid number of slices for TWAP order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            order_id = order_manager.submit_twap_order(side, int(qty), float(twap_duration), int(twap_slices), now)
            
        elif order_type == 'Trickle':
            if None in [trickle_max_slice, trickle_min_slice, trickle_max_pause, trickle_min_pause]:
                return n_clicks, side, qty, "Error: Invalid parameters for trickle order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            if trickle_min_slice > trickle_max_slice or trickle_min_slice <= 0:
                return n_clicks, side, qty, "Error: Invalid slice sizes for trickle order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            if trickle_min_pause > trickle_max_pause or trickle_min_pause <= 0:
                return n_clicks, side, qty, "Error: Invalid pause times for trickle order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
            order_id = order_manager.submit_trickle_order(side, int(qty), int(trickle_max_slice), int(trickle_min_slice),
                                                     float(trickle_max_pause), float(trickle_min_pause), now)

        if order_id is not None:
            success_msg = f"Order submitted: {order_type} {side} {qty}"
            if order_type in ['Limit', 'Iceberg']:
                success_msg += f" @ {price}"
            print(f"[Order Submitted] {success_msg}")
            return 0, 'Buy', 10, success_msg, {**base_status_style, 'backgroundColor': '#e8f5e9', 'color': '#2e7d32'}
        
        return n_clicks, side, qty, "Error: Failed to create order", {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}
        
    except Exception as e:
        error_msg = f"Error submitting order: {str(e)}"
        print(f"[Order Callback Error] {error_msg}")
        return n_clicks, side, qty, error_msg, {**base_status_style, 'backgroundColor': '#ffebee', 'color': '#c62828'}

@app.callback(
    Output('live-candles', 'figure'),
    Input('update-interval', 'n_intervals')
)
def update_chart(_):
    try:
        if agg is None:
            return go.Figure()
        df = agg.to_dataframe()
        if df.empty:
            return go.Figure()
            
        df = df.tail(50)
        
        df.index = pd.to_datetime(df.index, unit='s')
        
        fig = go.Figure(go.Candlestick(
            x=df.index,
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            increasing_line_color='black',
            decreasing_line_color='black',
            xaxis='x',
            yaxis='y'
        ))
        
        fig.update_layout(
            title='Price Chart',
            yaxis_title='Price',
            xaxis_title='Time',
            template='plotly_white',
            height=400,
            margin=dict(l=50, r=50, t=50, b=50),
            xaxis=dict(
                type='date',
                rangeslider=dict(visible=False),
                gridcolor='#e1e1e1',
                showgrid=True,
            ),
            yaxis=dict(
                gridcolor='#e1e1e1',
                showgrid=True,
            ),
            plot_bgcolor='white',
            paper_bgcolor='white'
        )

        fig.update_traces(
            selector=dict(type='candlestick'),
            whiskerwidth=0,
            line=dict(width=1),
            increasing_line_color='black',
            decreasing_line_color='black',
            increasing_fillcolor='rgba(0, 255, 0, 0.3)',
            decreasing_fillcolor='rgba(255, 0, 0, 0.3)',
            xperiod=1000,
            xperiodalignment="middle"
        )
        
        return fig
    except Exception as e:
        print("[Chart Callback Error]", e)
        return go.Figure()

@app.callback(
    [Output('orderbook-content', 'children'),
     Output('market-trade-tape', 'children'),
     Output('user-trade-tape', 'children')],
    Input('update-interval', 'n_intervals')
)
def update_market_data(_):
    if orderbook is None:
        return html.Div("Initializing..."), html.Div("Initializing..."), html.Div("Initializing...")
    
    try:
        levels = get_orderbook_table_snapshot(orderbook)
        
        bids = [level for level in levels if level['side'] == 'Bid']
        asks = [level for level in levels if level['side'] == 'Ask']
        
        asks = sorted(asks, key=lambda x: x['price'], reverse=True)
        bids = sorted(bids, key=lambda x: x['price'], reverse=True)

        total_bid_qty = sum(level['quantity'] for level in bids)
        total_ask_qty = sum(level['quantity'] for level in asks)

        orderbook_display = html.Div([
            html.Div([
                html.Div([
                    html.Div('Bids', style={'textAlign': 'center', 'color': '#008000'}),
                    html.Div(f'Total: {total_bid_qty}', style={'textAlign': 'center', 'fontSize': '0.9em', 'color': '#006400'})
                ], style={'width': '50%', 'display': 'inline-block'}),
                html.Div([
                    html.Div('Asks', style={'textAlign': 'center', 'color': '#ff0000'}),
                    html.Div(f'Total: {total_ask_qty}', style={'textAlign': 'center', 'fontSize': '0.9em', 'color': '#8b0000'})
                ], style={'width': '50%', 'display': 'inline-block'}),
            ], style={'display': 'flex', 'marginBottom': '10px'}),
            
            html.Div([
                html.Div([
                    html.Div([
                        html.Span(f"{level['quantity']}", style={'marginRight': '10px', 'color': '#000000'}),
                        html.Span(format_price(level['price']), style={'color': '#008000'})
                    ], style={'marginBottom': '5px'})
                    for level in bids
                ], style={'width': '50%', 'display': 'inline-block', 'verticalAlign': 'top', 'textAlign': 'right', 'paddingRight': '10px'}),
                
                html.Div([
                    html.Div([
                        html.Span(f"{level['quantity']}", style={'marginRight': '10px', 'color': '#000000'}),
                        html.Span(format_price(level['price']), style={'color': '#ff0000'})
                    ], style={'marginBottom': '5px'})
                    for level in asks
                ], style={'width': '50%', 'display': 'inline-block', 'verticalAlign': 'top', 'textAlign': 'right'})
            ], style={'display': 'flex'})
        ], style={'backgroundColor': 'white', 'padding': '10px', 'fontFamily': 'monospace'})
        
        trade_list = []
        for trade in recent_trades:
            color = '#008000' if trade['side'] == 'Buy' else '#ff0000'
            trade_list.append(html.Div([
                html.Span(f"{trade['side']} ", style={'color': color}),
                html.Span(f"{trade['quantity']} @ {format_price(trade['price'])}", style={'color': '#000000'})
            ], style={'marginBottom': '5px', 'fontFamily': 'monospace'}))
            
        user_trade_list = []
        for trade in user_trades:
            color = '#008000' if trade['side'] == 'Buy' else '#ff0000'
            trade_text = [
                html.Span(f"{trade['side']} ", style={'color': color}),
                html.Span(f"{trade['quantity']} @ {format_price(trade['price'])}", style={'color': '#000000'})
            ]
            if trade.get('type') == 'limit':
                trade_text.append(html.Span(" (Limit)", style={'color': '#666666', 'fontStyle': 'italic'}))
            user_trade_list.append(html.Div(trade_text, style={'marginBottom': '5px', 'fontFamily': 'monospace'}))
        
        return (
            orderbook_display, 
            html.Div(trade_list, style={'backgroundColor': 'white', 'padding': '10px'}),
            html.Div(user_trade_list, style={'backgroundColor': 'white', 'padding': '10px'})
        )
        
    except Exception as e:
        print("[Market Data Callback Error]", e)
        return (
            html.Div("Error loading orderbook"), 
            html.Div("Error loading trades"),
            html.Div("Error loading user trades")
        )

def run_simulation():
    print("Starting simulation...")
    
    global orderbook, agg
    orderbook = OrderBook()
    agg = DataAggregator(bar_interval=1.0)
    current_price = 100.0
    t = time.time()
    
    populate_limit_orders(orderbook, current_price, t)
    print("Initialized orderbook with orders")

    agg.start_new_bar(current_price, t)

    while True:
        try:
            t = time.time()
            
            agg.start_new_bar(current_price, t)
            
            orders_to_execute = order_manager.pull_orders(orderbook, t)
            
            for order in orders_to_execute:
                if order.getPrice() == 0.0:
                    trades = orderbook.matchOrder(order.getSide(), order.getPrice(), 
                                               order.getQuantity(), order.getTimeStamp())
                    if trades:
                        first_trade = trades[0]
                        trade_info = {
                            'side': 'Buy' if order.getSide() == OrderSide.Buy else 'Sell',
                            'price': first_trade.price,
                            'quantity': order.getQuantity(),
                            'timestamp': first_trade.timestamp
                        }
                        
                        user_trades.appendleft(trade_info)
                        recent_trades.appendleft(trade_info)
                        
                        for trade in trades:
                            print(f"Trade: {trade.price}, {trade.quantity}, {t}")
                            agg.update(trade.price, trade.quantity, t)
                else:
                    orderbook.insertOrder(order)
                    bb = orderbook.getBestBid()
                    ba = orderbook.getBestAsk()
                    if ((order.getSide() == OrderSide.Buy and order.getPrice() >= ba) or
                        (order.getSide() == OrderSide.Sell and order.getPrice() <= bb)):
                        trade_info = {
                            'side': 'Buy' if order.getSide() == OrderSide.Buy else 'Sell',
                            'price': order.getPrice(),
                            'quantity': order.getQuantity(),
                            'timestamp': t
                        }
                        user_trades.appendleft(trade_info)

            bb = orderbook.getBestBid()
            ba = orderbook.getBestAsk()
            if bb > 0 and ba > 0:
                current_price = (bb + ba) / 2

            target = simulate_jump_diffusion(current_price)
            print(f"Current: {current_price:.2f}, Target: {target:.2f}")

            raw_vol = get_volume_to_price(orderbook, current_price, target)

            splits = generate_signed_splits((1 if target > current_price else -1) * raw_vol)
            times = generate_hawkes_times(len(splits))
            
            for (sign, qty), dt_off in zip(splits, times):
                side = OrderSide.Buy if sign > 0 else OrderSide.Sell
                trades = orderbook.matchOrder(side, 0.0, qty, t + dt_off)
                
                for trade in trades:
                    agg.update(trade.price, trade.quantity, t)
                    recent_trades.appendleft({
                        'side': 'Buy' if side == OrderSide.Buy else 'Sell',
                        'price': trade.price,
                        'quantity': trade.quantity,
                        'timestamp': trade.timestamp
                    })

            current_price = target
            agg.update(current_price, 0, t)
            populate_limit_orders(orderbook, current_price, t)
            
            next_t = t + DT
            sleep_time = max(0, next_t - time.time())
            time.sleep(sleep_time)
            
        except Exception as e:
            print(f"Simulation error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    print("Starting HFT Engine...")
    sim_thread = threading.Thread(target=run_simulation, daemon=True)
    sim_thread.start()
    print("Simulation thread started, launching dashboard...")

    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    app.run(host='127.0.0.1', port=8050, debug=False) 