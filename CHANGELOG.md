# Changelog

## [0.1.0] - 2024-01-XX

### Added
- Complete restructuring of project into `obfeed` library package
- Core engine modules:
  - `MarketSimulator` - Jump-diffusion price simulation
  - `OptionChainGenerator` - Black-Scholes option pricing with volatility surface
  - `OrderBookManager` - Orderbook management (supports C++ backend or Python-only)
- REST API with FastAPI:
  - Feed control endpoints (start/stop/state)
  - Market data endpoints (quote, options)
  - Orderbook endpoints (snapshot, order submission)
  - Configuration management
- WebSocket support for real-time market data streaming
- Command-line interface (`obfeed` command):
  - `obfeed server` - Run API server
  - `obfeed feed` - Run standalone feed
- Docker support:
  - Updated Dockerfile
  - docker-compose.yml for easy deployment
- Comprehensive documentation:
  - README.md with full API documentation
  - QUICKSTART.md for quick setup
  - Example scripts and configurations
- Configuration system with dataclasses for type safety
- Examples directory with usage examples

### Changed
- Extracted core logic from `feed.py` into modular library structure
- Removed Kafka dependencies (now optional/pluggable)
- Made orderbook backend optional (works with or without C++ extensions)

### Removed
- Direct Kafka integration (can be added as plugin)
- Prometheus metrics integration (can be added separately)

### Migration Notes
- Old `feed.py` can still be used but new structure is recommended
- API is now REST-based instead of direct Kafka publishing
- Configuration is now done via API or config files instead of command-line only
