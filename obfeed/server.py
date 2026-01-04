"""Server entry point for OBFeed."""

import argparse
import uvicorn


def main():
    """Main entry point for the server."""
    parser = argparse.ArgumentParser(description="OBFeed - Synthetic Market Feed Server")
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    
    args = parser.parse_args()
    
    uvicorn.run(
        "obfeed.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
