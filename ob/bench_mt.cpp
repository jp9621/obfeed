#include "OrderBook.h"
#include "Order.h"
#include "Simulator.h"

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
// Workload
//
// Realistic market-maker workload where depth stays bounded:
//   30% passive limit insert  (non-crossing, adds to book)
//   30% cancel                (cancels a recently-inserted order)
//   40% getBestBid/Ask        (top-of-book read)
//
// Because insert ≈ cancel, at steady state the depth per level
// stabilises around the cancel-ring size (~256).  This is the
// bounded-depth regime where a ring buffer is supposed to win.
// ============================================================
static constexpr int    BENCH_MS  = 3000;
static constexpr int    N_LEVELS  = 20;
static constexpr double BID_BASE  = 90.0;
static constexpr double ASK_BASE  = 91.0;
static constexpr double TICK      = 0.01;
static constexpr int    PRELOAD   = 400;

struct Pool {
    static constexpr int SZ = 1 << 16;
    struct Entry { double price; int qty; OrderSide side; };
    Entry e[SZ];

    explicit Pool(int seed) {
        std::mt19937 rng(seed);
        std::uniform_int_distribution<int> qty(1, 100);
        std::uniform_int_distribution<int> lvl(0, N_LEVELS - 1);
        for (int i = 0; i < SZ; ++i) {
            OrderSide side = (i & 1) ? OrderSide::Buy : OrderSide::Sell;
            e[i].price = (side == OrderSide::Buy)
                       ? BID_BASE + lvl(rng) * TICK
                       : ASK_BASE + lvl(rng) * TICK;
            e[i].qty  = qty(rng);
            e[i].side = side;
        }
    }
};

struct Result { double mops; };

template<typename Book>
Result runBench(int nThreads) {
    std::vector<Pool> pools;
    pools.reserve(nThreads);
    for (int i = 0; i < nThreads; ++i)
        pools.emplace_back(i * 31337 + 1);

    Book book;
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
        const Pool& pool  = pools[tid];
        uint64_t&   cnt   = counts[tid];
        std::mt19937 rng(tid * 999983 + 7);
        std::uniform_int_distribution<int> pct(0, 99);

        static constexpr int RING = 256;
        std::pair<int, double> ring[RING];
        for (auto& r : ring) r = {-1, 0.0};
        int ringHead = 0, orderCtr = 0, poolIdx = 0;
        const int idBase = tid * (1 << 20);

        while (!go.load(std::memory_order_acquire)) {}

        while (!stop.load(std::memory_order_relaxed)) {
            int roll = pct(rng);

            if (roll < 30) {
                // Passive limit insert
                const auto& tmpl = pool.e[poolIdx++ & (Pool::SZ - 1)];
                int id = idBase + (orderCtr++ & ((1 << 20) - 1));
                Order o(id, tmpl.price, tmpl.qty, tmpl.side, 0.0);
                book.insertOrder(o);
                ring[ringHead++ & (RING - 1)] = {id, tmpl.price};
            } else if (roll < 60) {
                // Cancel a recently inserted order
                int slot = rng() & (RING - 1);
                auto [oid, oprice] = ring[slot];
                if (oid >= 0) {
                    book.cancelOrder(oid, oprice);
                    ring[slot] = {-1, 0.0};
                }
            } else {
                // Top-of-book read
                if (roll & 1) (void)book.getBestBid();
                else          (void)book.getBestAsk();
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
// Option chain benchmark (single-threaded — mirrors real usage
// where one simulator thread drives build_chain on each tick)
// ============================================================
static double benchChain() {
    OptionChainConfig cfg;          // default: 9 expiries × 21 strikes = 378 quotes
    std::mt19937_64   rng(42);
    OptionChainGenerator gen(cfg, rng);

    // Bootstrap EWMA vol estimator (needs min_history_samples = 10)
    double price = 100.0;
    for (int i = 0; i < 15; ++i)
        gen.update_vol(price * (1.0 + 0.001 * (i - 7)), 1.0);

    std::atomic<bool> stop{false};
    uint64_t count = 0;

    auto t0 = std::chrono::steady_clock::now();
    std::this_thread::sleep_for(std::chrono::milliseconds(100)); // warmup

    count = 0;
    t0 = std::chrono::steady_clock::now();
    auto deadline = t0 + std::chrono::milliseconds(BENCH_MS);
    while (std::chrono::steady_clock::now() < deadline) {
        (void)gen.build_chain(price, 1.0e9);
        ++count;
    }
    double elapsed = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - t0).count();
    (void)stop;
    return count / elapsed;  // chains/sec
}

int main() {
    unsigned hw = std::thread::hardware_concurrency();
    std::vector<int> tcs = {1, 2, 4, 8};
    if (hw > 0 && std::find(tcs.begin(), tcs.end(), (int)hw) == tcs.end())
        tcs.push_back((int)hw);

    std::cout << "\n";
    std::cout << "╔══════════════════════════════════════════════════════╗\n";
    std::cout << "║       Order Book Multithreaded Benchmark             ║\n";
    std::cout << "╚══════════════════════════════════════════════════════╝\n";
    std::cout << "  Duration  : " << BENCH_MS << " ms per run\n";
    std::cout << "  Threads   : up to " << hw << "\n";
    std::cout << "  Workload  : 30% insert  |  30% cancel  |  40% read\n";
    std::cout << "  Depth     : bounded (~256 orders/level at steady state)\n";
    std::cout << "\n";
    std::cout << "  Naive  : std::mutex, single std::map, O(n) cancel\n";
    std::cout << "  Opt    : per-level mutex, flat array, O(1) cancel\n";
    std::cout << "\n";

    std::cout << std::left
              << std::setw(10) << "Threads"
              << std::setw(18) << "Naive (Mops/s)"
              << std::setw(18) << "Opt   (Mops/s)"
              << "Speedup\n";
    std::cout << std::string(56, '-') << "\n";

    for (int nt : tcs) {
        auto naive = runBench<NaiveOrderBook>(nt);
        auto opt   = runBench<OrderBook>(nt);
        double speedup = opt.mops / naive.mops;

        std::cout << std::left  << std::setw(10) << nt
                  << std::fixed << std::setprecision(3)
                  << std::setw(18) << naive.mops
                  << std::setw(18) << opt.mops;

        if      (speedup >= 1.05) std::cout << "\033[32m" << speedup << "x\033[0m";
        else if (speedup >= 0.95) std::cout << "\033[33m" << speedup << "x\033[0m";
        else                      std::cout << "\033[31m" << speedup << "x\033[0m";
        std::cout << "\n";
    }
    std::cout << "\n";

    // ── Option chain benchmark ───────────────────────────────
    std::cout << "╔══════════════════════════════════════════════════════╗\n";
    std::cout << "║       Option Chain Benchmark (single-threaded)       ║\n";
    std::cout << "╚══════════════════════════════════════════════════════╝\n";
    std::cout << "  Chain size : 9 expiries × 21 strikes × 2 sides = 378 quotes\n";
    std::cout << "  Duration   : " << BENCH_MS << " ms\n\n";

    double cps = benchChain();
    std::cout << std::fixed << std::setprecision(1);
    std::cout << "  build_chain throughput: " << cps << " chains/sec"
              << "  (" << std::setprecision(3) << (cps * 378 / 1e6) << " Mquotes/sec)\n\n";

    return 0;
}
