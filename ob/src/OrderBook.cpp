#include "OrderBook.h"
#include "Order.h"
#include "Trade.h"
#include <algorithm>
#include <cmath>
#include <limits>

// ─────────────────────────────────────────────────────────────
// Construction
// ─────────────────────────────────────────────────────────────

OrderBook::OrderBook(double basePrice, double tickSize, int numTicks)
    : _basePrice(basePrice)
    , _invTickSize(1.0 / tickSize)
    , _tickSize(tickSize)
    , _numTicks(numTicks)
    , _levels(std::make_unique<Level[]>(numTicks))
    , _bestBidTick(-1)
    , _bestAskTick(numTicks)
{}

// ─────────────────────────────────────────────────────────────
// Price ↔ tick helpers
// ─────────────────────────────────────────────────────────────

inline int OrderBook::toTick(double price) const noexcept {
    return static_cast<int>((price - _basePrice) * _invTickSize + 0.5);
}

inline double OrderBook::toPrice(int tick) const noexcept {
    return _basePrice + tick * _tickSize;
}

inline bool OrderBook::validTick(int tick) const noexcept {
    return tick >= 0 && tick < _numTicks;
}

// ─────────────────────────────────────────────────────────────
// Best-price refresh (called after a level empties)
// Caller holds the emptied level's mutex; we only read atomic
// quantities on other levels — no other locks taken.
// ─────────────────────────────────────────────────────────────

void OrderBook::refreshBestBidFrom(int startTick) {
    for (int t = startTick; t >= 0; --t) {
        if (_levels[t].bidQty.load(std::memory_order_acquire) > 0) {
            _bestBidTick.store(t, std::memory_order_release);
            return;
        }
    }
    _bestBidTick.store(-1, std::memory_order_release);
}

void OrderBook::refreshBestAskFrom(int startTick) {
    for (int t = startTick; t < _numTicks; ++t) {
        if (_levels[t].askQty.load(std::memory_order_acquire) > 0) {
            _bestAskTick.store(t, std::memory_order_release);
            return;
        }
    }
    _bestAskTick.store(_numTicks, std::memory_order_release);
}

// ─────────────────────────────────────────────────────────────
// insertOrder
// ─────────────────────────────────────────────────────────────

void OrderBook::insertOrder(const Order& order) {
    if (order.getPrice() == 0.0) {
        matchOrder(order.getSide(), 0.0, order.getQuantity(), order.getTimeStamp());
        return;
    }

    int tick = toTick(order.getPrice());
    if (!validTick(tick)) return;

    const OrderSide side = order.getSide();

    // Check for a crossing order using the atomic best-price — no lock needed.
    if (side == OrderSide::Sell) {
        int bb = _bestBidTick.load(std::memory_order_acquire);
        if (bb >= 0 && tick <= bb) {
            matchOrder(side, order.getPrice(), order.getQuantity(), order.getTimeStamp());
            return;
        }
    } else {
        int ba = _bestAskTick.load(std::memory_order_acquire);
        if (ba < _numTicks && tick >= ba) {
            matchOrder(side, order.getPrice(), order.getQuantity(), order.getTimeStamp());
            return;
        }
    }

    // Resting insert: lock only this one level.
    Level& lvl = _levels[tick];
    {
        std::lock_guard<std::mutex> lk(lvl.mtx);

        if (side == OrderSide::Buy) {
            lvl.bids.push_back(order);
            int prev = lvl.bidQty.fetch_add(order.getQuantity(), std::memory_order_release);
            if (prev == 0) {
                // Level just became non-empty — update best bid upward via CAS.
                int expected = _bestBidTick.load(std::memory_order_relaxed);
                while (tick > expected &&
                       !_bestBidTick.compare_exchange_weak(
                           expected, tick,
                           std::memory_order_release,
                           std::memory_order_relaxed)) {}
            }
        } else {
            lvl.asks.push_back(order);
            int prev = lvl.askQty.fetch_add(order.getQuantity(), std::memory_order_release);
            if (prev == 0) {
                // Level just became non-empty — update best ask downward via CAS.
                int expected = _bestAskTick.load(std::memory_order_relaxed);
                while (tick < expected &&
                       !_bestAskTick.compare_exchange_weak(
                           expected, tick,
                           std::memory_order_release,
                           std::memory_order_relaxed)) {}
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────
// cancelOrder
// ─────────────────────────────────────────────────────────────

bool OrderBook::cancelOrder(int orderId, double price) {
    int tick = toTick(price);
    if (!validTick(tick)) return false;

    Level& lvl = _levels[tick];
    std::lock_guard<std::mutex> lk(lvl.mtx);

    for (auto it = lvl.bids.begin(); it != lvl.bids.end(); ++it) {
        if (it->getId() == orderId) {
            int qty = it->getQuantity();
            lvl.bids.erase(it);
            lvl.bidQty.fetch_sub(qty, std::memory_order_release);
            if (lvl.bidQty.load(std::memory_order_acquire) == 0 &&
                tick == _bestBidTick.load(std::memory_order_relaxed)) {
                refreshBestBidFrom(tick - 1);
            }
            return true;
        }
    }
    for (auto it = lvl.asks.begin(); it != lvl.asks.end(); ++it) {
        if (it->getId() == orderId) {
            int qty = it->getQuantity();
            lvl.asks.erase(it);
            lvl.askQty.fetch_sub(qty, std::memory_order_release);
            if (lvl.askQty.load(std::memory_order_acquire) == 0 &&
                tick == _bestAskTick.load(std::memory_order_relaxed)) {
                refreshBestAskFrom(tick + 1);
            }
            return true;
        }
    }
    return false;
}

// ─────────────────────────────────────────────────────────────
// matchOrder
// ─────────────────────────────────────────────────────────────

std::vector<Trade> OrderBook::matchOrder(OrderSide side, double price,
                                         int quantity, double timestamp) {
    std::vector<Trade> allTrades;
    int remaining = quantity;

    if (side == OrderSide::Buy) {
        int limitTick = (price == 0.0) ? (_numTicks - 1) : toTick(price);
        int startTick = _bestAskTick.load(std::memory_order_acquire);

        for (int t = startTick; t <= limitTick && remaining > 0 && t < _numTicks; ++t) {
            if (_levels[t].askQty.load(std::memory_order_relaxed) == 0) continue;

            Level& lvl = _levels[t];
            std::lock_guard<std::mutex> lk(lvl.mtx);

            while (!lvl.asks.empty() && remaining > 0) {
                Order& ask     = lvl.asks.front();
                int    matched = std::min(remaining, ask.getQuantity());
                Trade  trade(toPrice(t), matched, OrderSide::Buy, timestamp);
                updatePosition(trade);
                allTrades.push_back(trade);
                remaining -= matched;
                if (matched == ask.getQuantity()) {
                    lvl.asks.pop_front();
                } else {
                    ask.setQuantity(ask.getQuantity() - matched);
                }
                lvl.askQty.fetch_sub(matched, std::memory_order_release);
            }
            if (lvl.askQty.load(std::memory_order_acquire) == 0 &&
                t == _bestAskTick.load(std::memory_order_relaxed)) {
                refreshBestAskFrom(t + 1);
            }
        }
    } else {
        int limitTick = (price == 0.0) ? 0 : toTick(price);
        int startTick = _bestBidTick.load(std::memory_order_acquire);

        for (int t = startTick; t >= limitTick && remaining > 0 && t >= 0; --t) {
            if (_levels[t].bidQty.load(std::memory_order_relaxed) == 0) continue;

            Level& lvl = _levels[t];
            std::lock_guard<std::mutex> lk(lvl.mtx);

            while (!lvl.bids.empty() && remaining > 0) {
                Order& bid     = lvl.bids.front();
                int    matched = std::min(remaining, bid.getQuantity());
                Trade  trade(toPrice(t), matched, OrderSide::Sell, timestamp);
                updatePosition(trade);
                allTrades.push_back(trade);
                remaining -= matched;
                if (matched == bid.getQuantity()) {
                    lvl.bids.pop_front();
                } else {
                    bid.setQuantity(bid.getQuantity() - matched);
                }
                lvl.bidQty.fetch_sub(matched, std::memory_order_release);
            }
            if (lvl.bidQty.load(std::memory_order_acquire) == 0 &&
                t == _bestBidTick.load(std::memory_order_relaxed)) {
                refreshBestBidFrom(t - 1);
            }
        }
    }

    // Place any unmatched remainder as a resting limit order.
    if (remaining > 0 && price > 0.0) {
        Order remainder(static_cast<int>(timestamp), price, remaining, side, timestamp);
        insertOrder(remainder);
    }

    return allTrades;
}

// ─────────────────────────────────────────────────────────────
// Position & P&L
// ─────────────────────────────────────────────────────────────

void OrderBook::updatePosition(const Trade& trade) {
    std::lock_guard<std::mutex> lk(_posMtx);

    int positionDelta = trade.side == OrderSide::Buy ? trade.quantity : -trade.quantity;

    if ((_position > 0 && positionDelta < 0) || (_position < 0 && positionDelta > 0)) {
        double avgPrice = _totalCost / std::abs(_position);
        if (_position > 0) {
            _realizedPnL += (trade.price - avgPrice) *
                            std::min(std::abs(positionDelta), std::abs(_position));
        } else {
            _realizedPnL += (avgPrice - trade.price) *
                            std::min(std::abs(positionDelta), std::abs(_position));
        }
    }

    _position   += positionDelta;
    _totalCost  += positionDelta * trade.price;

    if ((_position > 0 && _position - positionDelta < 0) ||
        (_position < 0 && _position - positionDelta > 0)) {
        _totalCost = _position * trade.price;
    }
}

double OrderBook::getUnrealizedPnL(double currentPrice) const {
    std::lock_guard<std::mutex> lk(_posMtx);
    if (_position == 0) return 0.0;
    double avgPrice = _totalCost / std::abs(_position);
    return _position > 0
        ? (currentPrice - avgPrice) * _position
        : (avgPrice - currentPrice) * std::abs(_position);
}

// ─────────────────────────────────────────────────────────────
// Best bid / ask  —  pure atomic loads, no lock
// ─────────────────────────────────────────────────────────────

double OrderBook::getBestBid() const {
    int tick = _bestBidTick.load(std::memory_order_acquire);
    return tick >= 0 ? toPrice(tick) : 0.0;
}

double OrderBook::getBestAsk() const {
    int tick = _bestAskTick.load(std::memory_order_acquire);
    return tick < _numTicks ? toPrice(tick) : std::numeric_limits<double>::max();
}

// ─────────────────────────────────────────────────────────────
// Legacy stubs
// ─────────────────────────────────────────────────────────────

PriceLevel* OrderBook::getPriceLevel(double /*price*/) {
    return nullptr;
}

std::map<double, PriceLevel*>& OrderBook::getSortedLevels() {
    return _legacyMap;
}
