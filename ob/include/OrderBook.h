#ifndef ORDERBOOK_H
#define ORDERBOOK_H

#include "Order.h"
#include "Trade.h"
#include "PriceLevel.h"  // kept for legacy getPriceLevel() return type

#include <deque>
#include <mutex>
#include <atomic>
#include <vector>
#include <map>
#include <memory>
#include <limits>

class OrderBook {
public:
    // basePrice : lowest representable price
    // tickSize  : price grid spacing (prices must be multiples of this)
    // numTicks  : total slots  → covers [basePrice, basePrice + numTicks*tickSize)
    OrderBook(double basePrice = 0.0,
              double tickSize  = 0.01,
              int    numTicks  = 50000);
    ~OrderBook() = default;

    void insertOrder(const Order& order);
    bool cancelOrder(int orderId, double price);
    std::vector<Trade> matchOrder(OrderSide side, double price, int quantity, double timestamp);

    void   updatePosition(const Trade& trade);
    double getUnrealizedPnL(double currentPrice) const;
    double getBestBid() const;
    double getBestAsk() const;

    // Legacy stubs — not meaningful with flat-array layout
    PriceLevel*                   getPriceLevel(double price);
    std::map<double, PriceLevel*>& getSortedLevels();

private:
    // One slot per tick.  bidQty/askQty are atomic so getBestBid/Ask can scan
    // without any lock.  The mutex only serialises order-queue mutations.
    struct Level {
        std::atomic<int>  bidQty{0};
        std::atomic<int>  askQty{0};
        std::mutex        mtx;
        std::deque<Order> bids;
        std::deque<Order> asks;

        Level() = default;
        Level(const Level&)            = delete;
        Level& operator=(const Level&) = delete;
    };

    int    toTick (double price) const noexcept;
    double toPrice(int    tick)  const noexcept;
    bool   validTick(int  tick)  const noexcept;

    // Scan for new best after a level empties.  Called while holding that level's
    // mutex; reads other levels' atomic quantities without locking them.
    void refreshBestBidFrom(int startTick);
    void refreshBestAskFrom(int startTick);

    double _basePrice;
    double _invTickSize;   // 1/tickSize — avoids repeated division
    double _tickSize;
    int    _numTicks;

    std::unique_ptr<Level[]> _levels;

    // O(1) lock-free best-price reads.
    // _bestBidTick == -1        → no bids in book
    // _bestAskTick == _numTicks → no asks in book
    std::atomic<int> _bestBidTick;
    std::atomic<int> _bestAskTick;

    // Position tracking is separated from per-level locks to avoid nesting.
    mutable std::mutex _posMtx;
    int    _position    = 0;
    double _realizedPnL = 0.0;
    double _totalCost   = 0.0;

    // Legacy stub — always empty
    std::map<double, PriceLevel*> _legacyMap;
};

#endif
