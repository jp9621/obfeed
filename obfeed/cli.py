"""Command-line interface for OBFeed."""

import argparse
import json
import sys
from typing import Optional

from obfeed.engine import MarketSimulator
from obfeed.config import EngineConfig, MarketSimConfig, OptionChainConfig


def run_feed(args):
    """Run the feed in standalone mode."""
    config = EngineConfig(
        symbol=args.symbol,
        venue=args.venue,
        initial_price=args.initial_price,
        rng_seed=args.seed,
        wall_clock_sleep=args.sleep,
    )
    
    if args.config_file:
        with open(args.config_file) as f:
            config_dict = json.load(f)
            # Merge config
            if "symbol" in config_dict:
                config.symbol = config_dict["symbol"]
            if "venue" in config_dict:
                config.venue = config_dict["venue"]
            if "initial_price" in config_dict:
                config.initial_price = config_dict["initial_price"]
            if "market" in config_dict:
                config.market = MarketSimConfig(**config_dict["market"])
    
    simulator = MarketSimulator(
        initial_price=config.initial_price,
        cfg=config.market,
        rng_seed=config.rng_seed,
    )
    
    print(f"Starting feed for {config.symbol} on {config.venue}")
    print(f"Initial price: {config.initial_price}")
    print(f"Press Ctrl+C to stop")
    print("-" * 60)
    
    try:
        step = 0
        while args.steps == 0 or step < args.steps:
            result = simulator.step()
            tick = result["tick"]
            
            if args.output == "json":
                print(json.dumps({
                    "step": step,
                    "tick": {
                        "ts": tick.ts,
                        "mid": tick.mid,
                        "bid": tick.bid,
                        "ask": tick.ask,
                    },
                    "trades": [
                        {
                            "ts": t.ts,
                            "price": t.price,
                            "qty": t.qty,
                            "side": t.side,
                        }
                        for t in result["trades"]
                    ],
                    "n_options": len(result.get("options", [])),
                }))
            else:
                print(f"[{tick.ts}] Mid: {tick.mid:.2f} | Bid: {tick.bid:.2f} | Ask: {tick.ask:.2f} | "
                      f"Trades: {len(result['trades'])} | Options: {len(result.get('options', []))}")
            
            if config.wall_clock_sleep > 0:
                import time
                time.sleep(config.wall_clock_sleep)
            
            step += 1
            
    except KeyboardInterrupt:
        print("\nStopping feed...")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="OBFeed - Synthetic Market Feed Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Feed command
    feed_parser = subparsers.add_parser("feed", help="Run market feed")
    feed_parser.add_argument("--symbol", default="SPY", help="Symbol name")
    feed_parser.add_argument("--venue", default="SIM", help="Venue identifier")
    feed_parser.add_argument("--initial-price", type=float, default=100.0, help="Initial price")
    feed_parser.add_argument("--seed", type=int, help="Random seed")
    feed_parser.add_argument("--sleep", type=float, default=0.01, help="Sleep between steps (seconds)")
    feed_parser.add_argument("--steps", type=int, default=0, help="Number of steps (0 = infinite)")
    feed_parser.add_argument("--config-file", help="JSON config file")
    feed_parser.add_argument("--output", choices=["text", "json"], default="text", help="Output format")
    feed_parser.set_defaults(func=run_feed)
    
    # Server command
    server_parser = subparsers.add_parser("server", help="Run API server")
    server_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    server_parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    server_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    
    args = parser.parse_args()
    
    if args.command == "server":
        from obfeed.server import main as server_main
        sys.argv = ["server", "--host", args.host, "--port", str(args.port)]
        if args.reload:
            sys.argv.append("--reload")
        server_main()
    elif args.command == "feed":
        run_feed(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
