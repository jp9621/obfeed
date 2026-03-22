#include "OrderBook.h"
#include "Order.h"

#include <map>
#include <deque>
#include <mutex>
#include <vector>
#include <atomic>
#include <thread>
#include <chrono>
#include <random>
#include <iostream>
#include <iomanip>
#include <limits>
#include <algorithm>

// ============================================================
// NaiveOrderBook
//   - std::mutex  (no shared reads – all ops serialize)
//   - single std::map (O(log n) price lookup, no hash-map fast path)
//   - no per-level locking
// ============================================================
class NaiveOrderBook {
public:
    void insertOrder(const Order& order) {
        std::lock_guard<std::mutex> lk(_mtx);
        auto& lvl = _book[order.getPrice()];
        if (order.getSide() == OrderSide::Buy) {
            lvl.bids.push_back(order);
            lvl.bidQty += order.getQuantity();
        } else {
            lvl.asks.push_back(order);
            lvl.askQty += order.getQuantity();
        }
    }

    bool cancelOrder(int orderId, double price) {
        std::lock_guard<std::mutex> lk(_mtx);
        auto it = _book.find(price);
        if (it == _book.end()) return false;
        auto& lvl = it->second;
        for (auto b = lvl.bids.begin(); b != lvl.bids.end(); ++b) {
            if (b->getId() == orderId) {
                lvl.bidQty -= b->getQuantity();
                lvl.bids.erase(b);
                if (lvl.isEmpty()) _book.erase(it);
                return true;
            }
        }
        for (auto a = lvl.asks.begin(); a != lvl.asks.end(); ++a) {
            if (a->getId() == orderId) {
                lvl.askQty -= a->getQuantity();
                lvl.asks.erase(a);
                if (lvl.isEmpty()) _book.erase(it);
                return true;
            }
        }
        return false;
    }

    double getBestBid() {
        std::lock_guard<std::mutex> lk(_mtx);
        for (auto it = _book.rbegin(); it != _book.rend(); ++it)
            if (it->second.bidQty > 0) return it->first;
        return 0.0;
    }

    double getBestAsk() {
        std::lock_guard<std::mutex> lk(_mtx);
        for (auto it = _book.begin(); it != _book.end(); ++it)
            if (it->second.askQty > 0) return it->first;
        return std::numeric_limits<double>::max();
    }

private:
    struct Level {
        std::deque<Order> bids, asks;
        int bidQty = 0, askQty = 0;
        bool isEmpty() const { return bids.empty() && asks.empty(); }
    };
    std::map<double, Level> _book;
    std::mutex _mtx;
};

// ============================================================
// Workload definitions
// ============================================================
enum class Workload { InsertOnly, ReadHeavy, Mixed };

static const char* wlLabel(Workload w) {
    switch (w) {
        case Workload::InsertOnly: return "Insert-Only (100% insert)";
        case Workload::ReadHeavy:  return "Read-Heavy  ( 80% read, 20% insert)";
        case Workload::Mixed:      return "Mixed       ( 50% insert, 30% read, 20% cancel)";
    }
    return "";
}

// ============================================================
// Pre-generated order pool – avoids RNG jitter on the hot path
//   Bids: 90.00 – 90.19   (20 levels, never cross asks)
//   Asks: 91.00 – 91.19   (20 levels, never cross bids)
// ============================================================
static constexpr int    POOL_SZ  = 1 << 16;  // 64 K, power-of-two for fast mod
static constexpr int    N_LEVELS = 20;
static constexpr double BID_BASE = 90.00;
static constexpr double ASK_BASE = 91.00;
static constexpr double TICK     = 0.01;

struct Pool {
    struct Entry { double price; int qty; OrderSide side; };
    Entry e[POOL_SZ];

    explicit Pool(int seed) {
        std::mt19937 rng(seed);
        std::uniform_int_distribution<int> qty(1, 100);
        std::uniform_int_distribution<int> lvl(0, N_LEVELS - 1);
        for (int i = 0; i < POOL_SZ; ++i) {
            OrderSide side = (i & 1) ? OrderSide::Buy : OrderSide::Sell;
            e[i].price = (side == OrderSide::Buy)
                       ? BID_BASE + lvl(rng) * TICK
                       : ASK_BASE + lvl(rng) * TICK;
            e[i].qty  = qty(rng);
            e[i].side = side;
        }
    }
};

// ============================================================
// Benchmark runner (templated so it works on both book types)
// ============================================================
static constexpr int BENCH_MS = 3000;
static constexpr int PRELOAD  = 400;   // orders seeded before the timer starts

struct Result { double mops; };

template<typename Book>
Result runBench(int nThreads, Workload wl) {
    // Build per-thread pools (avoids cache thrashing on a shared pool)
    std::vector<Pool> pools;
    pools.reserve(nThreads);
    for (int i = 0; i < nThreads; ++i)
        pools.emplace_back(i * 31337 + 1);

    Book book;

    // Seed the book so read ops and cancel ops have something to act on
    {
        std::mt19937 rng(0);
        std::uniform_int_distribution<int> qty(1, 100);
        for (int i = 0; i < PRELOAD; ++i) {
            book.insertOrder(Order(3'000'000 + i * 2,
                BID_BASE + (i % N_LEVELS) * TICK, qty(rng), OrderSide::Buy,  0.0));
            book.insertOrder(Order(3'000'000 + i * 2 + 1,
                ASK_BASE + (i % N_LEVELS) * TICK, qty(rng), OrderSide::Sell, 0.0));
        }
    }

    std::atomic<bool> go{false}, stop{false};
    std::vector<uint64_t> counts(nThreads, 0);

    auto worker = [&](int tid) {
        const Pool& pool = pools[tid];
        uint64_t&   cnt  = counts[tid];

        std::mt19937 rng(tid * 999983 + 7);
        std::uniform_int_distribution<int> pct(0, 99);

        // Cancel ring: remembers recently inserted (id, price) pairs
        static constexpr int RING = 256;
        std::pair<int, double> ring[RING];
        for (auto& r : ring) r = {-1, 0.0};
        int ringHead   = 0;
        int orderCtr   = 0;  // thread-local counter → unique order IDs
        int poolIdx    = 0;

        // The base ID block for this thread (avoid collision with seeding ids)
        const int idBase = tid * (1 << 20);  // 1M IDs per thread

        while (!go.load(std::memory_order_acquire)) {}

        while (!stop.load(std::memory_order_relaxed)) {
            int roll = pct(rng);

            bool doInsert = false, doRead = false, doCancel = false;
            switch (wl) {
                case Workload::InsertOnly:
                    doInsert = true; break;
                case Workload::ReadHeavy:
                    doRead   = roll < 80;
                    doInsert = !doRead; break;
                case Workload::Mixed:
                    doInsert = roll < 50;
                    doRead   = roll >= 50 && roll < 80;
                    doCancel = roll >= 80; break;
            }

            if (doInsert) {
                const auto& tmpl = pool.e[poolIdx & (POOL_SZ - 1)];
                int id = idBase + (orderCtr & ((1 << 20) - 1));
                Order o(id, tmpl.price, tmpl.qty, tmpl.side, 0.0);
                book.insertOrder(o);
                ring[ringHead & (RING - 1)] = {id, tmpl.price};
                ++ringHead;
                ++poolIdx;
                ++orderCtr;
            } else if (doRead) {
                if (roll & 1) (void)book.getBestBid();
                else          (void)book.getBestAsk();
            } else { // doCancel
                int slot = rng() & (RING - 1);
                auto [oid, oprice] = ring[slot];
                if (oid >= 0) {
                    book.cancelOrder(oid, oprice);
                    ring[slot] = {-1, 0.0};
                }
            }
            ++cnt;
        }
    };

    std::vector<std::thread> threads;
    threads.reserve(nThreads);
    for (int i = 0; i < nThreads; ++i)
        threads.emplace_back(worker, i);

    go.store(true, std::memory_order_release);
    auto t0 = std::chrono::steady_clock::now();
    std::this_thread::sleep_for(std::chrono::milliseconds(BENCH_MS));
    stop.store(true, std::memory_order_relaxed);
    for (auto& t : threads) t.join();

    double elapsed = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - t0).count();

    uint64_t total = 0;
    for (auto c : counts) total += c;
    return { total / elapsed / 1e6 };
}

// ============================================================
// main
// ============================================================
int main() {
    unsigned int hw = std::thread::hardware_concurrency();

    // Thread counts to test; deduplicated
    std::vector<int> tcs = {1, 2, 4, 8};
    if (hw > 0 && std::find(tcs.begin(), tcs.end(), (int)hw) == tcs.end())
        tcs.push_back((int)hw);

    const std::vector<Workload> workloads = {
        Workload::InsertOnly,
        Workload::ReadHeavy,
        Workload::Mixed,
    };

    std::cout << "\n";
    std::cout << "╔══════════════════════════════════════════════════════╗\n";
    std::cout << "║       Order Book Multithreaded Benchmark             ║\n";
    std::cout << "╚══════════════════════════════════════════════════════╝\n";
    std::cout << "  Duration per run : " << BENCH_MS   << " ms\n";
    std::cout << "  Hardware threads : " << hw          << "\n";
    std::cout << "  Price levels     : " << N_LEVELS * 2 << " (bid + ask)\n";
    std::cout << "  Preloaded orders : " << PRELOAD * 2  << "\n";
    std::cout << "\n";
    std::cout << "  Implementations compared\n";
    std::cout << "  ├─ Naive  : std::mutex, std::map only, no per-level lock\n";
    std::cout << "  └─ Opt    : std::shared_mutex, map + unordered_map, per-level lock\n";
    std::cout << "\n";

    for (auto wl : workloads) {
        std::cout << "┌─ " << wlLabel(wl) << "\n";
        std::cout << "│\n";
        std::cout << "│  " << std::left
                  << std::setw(9)  << "Threads"
                  << std::setw(17) << "Naive (Mops/s)"
                  << std::setw(17) << "Opt   (Mops/s)"
                  << std::setw(12) << "Speedup"
                  << "\n";
        std::cout << "│  " << std::string(55, '-') << "\n";

        for (int nt : tcs) {
            auto naive = runBench<NaiveOrderBook>(nt, wl);
            auto opt   = runBench<OrderBook>(nt, wl);
            double speedup = opt.mops / naive.mops;

            std::cout << "│  " << std::left << std::setw(9) << nt
                      << std::fixed << std::setprecision(3)
                      << std::setw(17) << naive.mops
                      << std::setw(17) << opt.mops;

            // ANSI color: green if faster, yellow if within 5%, red if slower
            if (speedup >= 1.05)
                std::cout << "\033[32m" << speedup << "x\033[0m";
            else if (speedup >= 0.95)
                std::cout << "\033[33m" << speedup << "x\033[0m";
            else
                std::cout << "\033[31m" << speedup << "x\033[0m";
            std::cout << "\n";
        }
        std::cout << "│\n";
    }
    std::cout << "└─ done\n\n";
    return 0;
}
