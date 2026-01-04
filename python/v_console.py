import numpy as np
import random
import math
from hft import OrderBook, Order, OrderSide

MU, SIGMA = 0.0, 0.01
LAMBDA, JUMP_MU, JUMP_SIGMA = 0.1, 0.0, 0.02
DT = 1.0  
HAWKES_MU, HAWKES_ALPHA, HAWKES_BETA = 0.5, 0.8, 1.0
LIMIT_BASE_QTY, DECAY_RATE = 100, 0.2
TICK_SIZE, NUM_LEVELS = 0.01, 10

_order_id = 0
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
    n = random.randint(1, min(10, abs(net_qty)))
    splits, running = [], 0
    for _ in range(n-1):
        q = random.randint(1, abs(net_qty))
        s = 1 if random.random() < 0.5 else -1
        splits.append((s, q))
        running += s*q
    last = net_qty - running
    splits.append((1 if last>=0 else -1, abs(last)))
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
    total_quantity_added = 0  
    for lvl in range(1, levels + 1):
        mean_qty = LIMIT_BASE_QTY * math.exp(-DECAY_RATE * lvl)
        buy_qty  = max(1, int(np.random.exponential(scale=mean_qty)))
        sell_qty = max(1, int(np.random.exponential(scale=mean_qty)))
        buy_price  = current_price - lvl * TICK_SIZE
        sell_price = current_price + lvl * TICK_SIZE
        ob.insertOrder(Order(next_order_id(), buy_price,  buy_qty,  OrderSide.Buy,  now))
        ob.insertOrder(Order(next_order_id(), sell_price, sell_qty, OrderSide.Sell, now))
        total_quantity_added += buy_qty + sell_qty  

    print(f"Total quantity added: {total_quantity_added}")  


def print_top_levels(ob, depth=5):
    lvl_map = ob.getSortedLevels()
    prices = sorted(lvl_map.keys())
    best_bid = ob.getBestBid()
    best_ask = ob.getBestAsk()
    print(f"best bid: {best_bid}, best ask: {best_ask}")
    sel_prices = []
    if best_bid > 0:
        sel_prices += [p for p in prices if best_bid - depth*TICK_SIZE <= p <= best_bid]
    if best_ask > 0:
        sel_prices += [p for p in prices if best_ask <= p <= best_ask + depth*TICK_SIZE]

    sel_prices = sorted(set(sel_prices), reverse=True)
    print("\n  BidQty  –   Price   – AskQty")
    for p in sel_prices:
        lvl = lvl_map[p]
        print(f"    {lvl.getBidQuantity():>5d} – {p:8.2f} – {lvl.getAskQuantity():>5d}")

def print_all_levels(ob):
    lvl_map = ob.getSortedLevels()
    print("\n  BidQty  –   Price   – AskQty")
    for p in sorted(lvl_map.keys(), reverse=True):
        lvl = lvl_map[p]
        print(f"    {lvl.getBidQuantity():>5d} – {p:8.2f} – {lvl.getAskQuantity():>5d}")

def main():
    ob = OrderBook()
    current_price = 100.0
    t = 0.0

    populate_limit_orders(ob, current_price, t)
    print("Initialization")
    print(f"  Current price: {current_price:.2f}")
    print_top_levels(ob)

    for step in range(1, 101):
        print(f"\nStep {step}")
        print("  Current orderbook:")
        print_top_levels(ob)

        while True:
            ans = input("Place market orders? (y/n) ").strip().lower()
            if ans != 'y':
                break
            side = input("  Side [b=buy, s=sell]: ").strip().lower()
            qty = int(input("  Quantity: ").strip())
            s = OrderSide.Buy if side == 'b' else OrderSide.Sell
            ob.matchOrder(s, 0.0, qty, t)
            print_top_levels(ob)

        bb = ob.getBestBid()
        ba = ob.getBestAsk()
        if bb > 0 and ba > 0:
            current_price = (bb + ba) / 2
        else:
            print("Warning: missing best bid/ask; price unchanged.")

        target = simulate_jump_diffusion(current_price)
        print(f"  New midpoint: {current_price:.2f}, Target: {target:.2f}")

        raw_vol = get_volume_to_price(ob, current_price, target)

        splits = generate_signed_splits((1 if target > current_price else -1) * raw_vol)
        times = generate_hawkes_times(len(splits))
        for (sign, qty), dt_off in zip(splits, times):
            side = OrderSide.Buy if sign > 0 else OrderSide.Sell
            ob.matchOrder(side, 0.0, qty, t + dt_off)

        current_price = target
        t += DT
        populate_limit_orders(ob, current_price, t)

    print("\nSimulation finished.")

if __name__ == "__main__":
    main()
