# Changelog

## [0.1.0] - 2024-01-XX

### Added
- Complete restructuring of project into `obfeed` service
- Core engine modules:
  - `MarketSimulator` - Jump-diffusion price simulation
  - `OptionChainGenerator` - Black-Scholes option pricing with volatility surface
  - `OrderBookManager` - Orderbook management (requires C++ ob module)
- REST API with FastAPI:
  - Market data endpoints (quote, options)
  - Orderbook endpoints (snapshot, order submission)
  - Feed auto-starts when service launches (like real market feeds)
- WebSocket support for real-time market data streaming
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
- Extracted core logic into modular library structure
- Removed Kafka dependencies (now optional/pluggable)
- Orderbook backend now requires C++ ob module (no Python fallback)

### Removed
- Direct Kafka integration (can be added as plugin)
- Prometheus metrics integration (can be added separately)

### Migration Notes
- API is now REST-based instead of direct Kafka publishing
- Configuration is now done via API
- Service runs as containerized service only (no CLI)
