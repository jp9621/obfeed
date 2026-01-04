"""Basic usage example for OBFeed."""

from obfeed import MarketSimulator, MarketSimConfig

# Create a simulator with custom configuration
config = MarketSimConfig(
    sigma=0.0001,  # Volatility
    trade_intensity=2.0,  # Trades per second
    dt=0.5,  # Time step in seconds
)

simulator = MarketSimulator(initial_price=100.0, cfg=config)

# Run simulation for 10 steps
print("Running simulation...")
for i in range(10):
    result = simulator.step()
    tick = result["tick"]
    trades = result["trades"]
    
    print(f"Step {i+1}:")
    print(f"  Mid: {tick.mid:.2f}")
    print(f"  Bid: {tick.bid:.2f}, Ask: {tick.ask:.2f}")
    print(f"  Trades: {len(trades)}")
    if trades:
        for trade in trades[:3]:  # Show first 3 trades
            print(f"    {trade.side} {trade.qty} @ {trade.price:.2f}")
    print()
