#ifndef ORDERBOOK_H
#define ORDERBOOK_H

#include "Order.h"
#include "Trade.h"
#include "PriceLevel.h"  // kept for legacy getPriceLevel() return type

#include <optional>
#include <unordered_map>
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
    // Pre-allocated circular buffer for one side of a price level.
    // Depth stays bounded in production (orders are matched or cancelled),
    // so the backing array rarely grows past its initial capacity.
    // Cancelled slots become nullopt and are swept lazily on the next pop.
    // O(1) insert, O(1) cancel (via hash index), O(1) amortised pop.
    struct RingBuf {
        std::vector<std::optional<Order>>  data;
        std::unordered_map<int, uint32_t>  idx;   // orderId → slot
        uint32_t head = 0;
        uint32_t sz   = 0;

        explicit RingBuf(uint32_t initCap = 64) : data(initCap) {}

        uint32_t cap()            const { return (uint32_t)data.size(); }
        bool     empty()          const { return sz == 0; }
        uint32_t slot(uint32_t p) const { return p % cap(); }

        void push(const Order& o) {
            if (sz == cap()) grow();
            uint32_t s = slot(head + sz);
            data[s] = o;
            idx[o.getId()] = s;
            ++sz;
        }

        // Advance past any nullopt (cancelled) slots; return front or nullptr.
        Order* front_valid() {
            while (sz > 0 && !data[head].has_value()) {
                head = slot(head + 1);
                --sz;
            }
            return sz > 0 ? &data[head].value() : nullptr;
        }

        // Remove the front entry (call only after front_valid() != nullptr).
        void consume_front() {
            idx.erase(data[head]->getId());
            data[head] = std::nullopt;
            head = slot(head + 1);
            --sz;
        }

        // Cancel by ID; returns cancelled qty or 0.
        int cancel(int id) {
            auto it = idx.find(id);
            if (it == idx.end()) return 0;
            int qty = data[it->second]->getQuantity();
            data[it->second] = std::nullopt;
            idx.erase(it);
            // Sweep nullopt holes from the front to keep sz tight.
            // Prevents unbounded sz growth (and consequent grow() calls)
            // when orders near the front are cancelled.
            while (sz > 0 && !data[head].has_value()) {
                head = slot(head + 1);
                --sz;
            }
            return qty;
        }

    private:
        void grow() {
            uint32_t oldCap = cap();
            uint32_t newCap = oldCap * 2;
            std::vector<std::optional<Order>> newData(newCap);
            idx.clear();
            uint32_t newSz = 0;
            for (uint32_t i = 0; i < sz; ++i) {
                uint32_t s = (head + i) % oldCap;
                if (data[s].has_value()) {
                    newData[newSz] = std::move(data[s]);
                    idx[newData[newSz]->getId()] = newSz;
                    ++newSz;
                }
            }
            data = std::move(newData);
            head = 0;
            sz   = newSz;
        }
    };

    // One slot per tick.  bidQty/askQty are atomic so getBestBid/Ask can scan
    // without any lock.  The mutex only serialises order-queue mutations.
    struct Level {
        std::atomic<int> bidQty{0};
        std::atomic<int> askQty{0};
        std::mutex       mtx;
        RingBuf          bids;
        RingBuf          asks;

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
