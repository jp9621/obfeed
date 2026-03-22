// bench_mt.cpp — multithreaded order book throughput benchmark
//
// Measures how both books scale when N threads hammer the same book
// concurrently. Each thread submits its own pre-generated workload;
// all threads start simultaneously via a manual spinbarrier.
//
// Build (from ob/build):
//   cmake .. && cmake --build . --target bench_mt
// Run:
//   ./bench_mt

#include "OrderBook.h"
#include "Order.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <deque>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <random>
#include <thread>
#include <vector>

// ─────────────────────────────────────────────────────────────────────────────
// Naive order book (identical to bench.cpp)
// ─────────────────────────────────────────────────────────────────────────────

struct NaiveOrder {
    int       id;
    double    price;
    int       qty;
    OrderSide side;
};

class NaiveOrderBook {
public:
    std::map<double, std::deque<NaiveOrder*>, std::greater<double>> _bids;
    std::map<double, std::deque<NaiveOrder*>>                       _asks;
    mutable std::mutex _mtx;

    ~NaiveOrderBook() {
        for (auto& [p, q] : _bids) for (auto* o : q) delete o;
        for (auto& [p, q] : _asks) for (auto* o : q) delete o;
    }

    void insert(int id, double price, int qty, OrderSide side) {
        std::lock_guard<std::mutex> lk(_mtx);
        if (side == OrderSide::Buy) {
            while (!_asks.empty() && price >= _asks.begin()->first && qty > 0) {
                auto& [ap, aq] = *_asks.begin();
                while (!aq.empty() && qty > 0) {
                    int fill = std::min(qty, aq.front()->qty);
                    qty -= fill;
                    aq.front()->qty -= fill;
                    if (aq.front()->qty == 0) { delete aq.front(); aq.pop_front(); }
                }
                if (aq.empty()) _asks.erase(_asks.begin());
            }
            if (qty > 0) _bids[price].push_back(new NaiveOrder{id, price, qty, side});
        } else {
            while (!_bids.empty() && price <= _bids.begin()->first && qty > 0) {
                auto& [bp, bq] = *_bids.begin();
                while (!bq.empty() && qty > 0) {
                    int fill = std::min(qty, bq.front()->qty);
                    qty -= fill;
                    bq.front()->qty -= fill;
                    if (bq.front()->qty == 0) { delete bq.front(); bq.pop_front(); }
                }
                if (bq.empty()) _bids.erase(_bids.begin());
            }
            if (qty > 0) _asks[price].push_back(new NaiveOrder{id, price, qty, side});
        }
    }

    bool cancel(int id, double price, OrderSide side) {
        std::lock_guard<std::mutex> lk(_mtx);
        if (side == OrderSide::Buy) {
            auto it = _bids.find(price);
            if (it == _bids.end()) return false;
            auto& q = it->second;
            for (auto qit = q.begin(); qit != q.end(); ++qit) {
                if ((*qit)->id == id) { delete *qit; q.erase(qit); return true; }
            }
        } else {
            auto it = _asks.find(price);
            if (it == _asks.end()) return false;
            auto& q = it->second;
            for (auto qit = q.begin(); qit != q.end(); ++qit) {
                if ((*qit)->id == id) { delete *qit; q.erase(qit); return true; }
            }
        }
        return false;
    }
};

// ─────────────────────────────────────────────────────────────────────────────
// Workload (same as bench.cpp)
// ─────────────────────────────────────────────────────────────────────────────

struct Op {
    enum Type { INSERT, CANCEL } type;
    int       id;
    double    price;
    int       qty;
    OrderSide side;
};

static std::vector<Op> make_workload(int n, uint64_t seed) {
    std::mt19937_64 rng(seed);
    std::uniform_real_distribution<double> price_dist(10.0, 990.0);
    std::uniform_int_distribution<int>     qty_dist(1, 100);
    std::uniform_int_distribution<int>     side_dist(0, 1);
    std::uniform_real_distribution<double> op_dist(0.0, 1.0);

    struct LiveOrder { int id; double price; OrderSide side; };
    std::vector<LiveOrder> live;
    live.reserve(n / 2);

    std::vector<Op> ops;
    ops.reserve(n);
    int next_id = 1;

    for (int i = 0; i < n; ++i) {
        if (!live.empty() && op_dist(rng) < 0.15) {
            std::uniform_int_distribution<size_t> pick(0, live.size() - 1);
            size_t idx = pick(rng);
            ops.push_back({Op::CANCEL, live[idx].id, live[idx].price, 0, live[idx].side});
            live.erase(live.begin() + idx);
        } else {
            double raw   = price_dist(rng);
            double price = std::round(raw * 100.0) / 100.0;
            int    qty   = qty_dist(rng);
            OrderSide side = (side_dist(rng) == 0) ? OrderSide::Buy : OrderSide::Sell;
            int id = next_id++;
            ops.push_back({Op::INSERT, id, price, qty, side});
            live.push_back({id, price, side});
        }
    }
    return ops;
}

// ─────────────────────────────────────────────────────────────────────────────
// Spinbarrier — all threads spin until every thread has checked in, then go
// ─────────────────────────────────────────────────────────────────────────────

struct SpinBarrier {
    std::atomic<int>  arrived{0};
    std::atomic<bool> go{false};
    const int         total;

    explicit SpinBarrier(int n) : total(n) {}

    void wait() {
        arrived.fetch_add(1, std::memory_order_release);
        while (arrived.load(std::memory_order_acquire) < total)
            ; // spin
        go.store(true, std::memory_order_release);
        while (!go.load(std::memory_order_acquire))
            ; // spin until coordinator broadcasts
    }
};

// ─────────────────────────────────────────────────────────────────────────────
// Book adapters — uniform interface for both implementations
// ─────────────────────────────────────────────────────────────────────────────

static void run_op(OrderBook& ob, const Op& op) {
    if (op.type == Op::INSERT)
        ob.insertOrder(Order(op.id, op.price, op.qty, op.side, 0.0));
    else
        ob.cancelOrder(op.id, op.price);
}

static void run_op(NaiveOrderBook& nb, const Op& op) {
    if (op.type == Op::INSERT)
        nb.insert(op.id, op.price, op.qty, op.side);
    else
        nb.cancel(op.id, op.price, op.side);
}

// ─────────────────────────────────────────────────────────────────────────────
// Timed multithreaded run
//   Returns total ops/s (all threads combined) for the median of N_RUNS runs.
// ─────────────────────────────────────────────────────────────────────────────

template<typename Book>
static double run_mt(int n_threads, int ops_per_thread, int n_runs, uint64_t base_seed) {
    std::vector<double> elapsed_ns(n_runs);

    for (int r = 0; r < n_runs; ++r) {
        // Pre-generate one workload per thread (different seeds → different price
        // sequences, same distribution — avoids all threads hammering the same levels).
        std::vector<std::vector<Op>> workloads(n_threads);
        for (int t = 0; t < n_threads; ++t)
            workloads[t] = make_workload(ops_per_thread, base_seed + r * 100 + t);

        Book book;
        SpinBarrier barrier(n_threads);

        // Per-thread end timestamps; we measure wall time = max(end) - min(start).
        std::vector<std::chrono::steady_clock::time_point> t_start(n_threads);
        std::vector<std::chrono::steady_clock::time_point> t_end(n_threads);

        std::vector<std::thread> threads;
        threads.reserve(n_threads);

        for (int t = 0; t < n_threads; ++t) {
            threads.emplace_back([&, t] {
                barrier.wait();
                t_start[t] = std::chrono::steady_clock::now();
                for (const auto& op : workloads[t])
                    run_op(book, op);
                t_end[t] = std::chrono::steady_clock::now();
            });
        }

        for (auto& th : threads) th.join();

        auto wall_start = *std::min_element(t_start.begin(), t_start.end());
        auto wall_end   = *std::max_element(t_end.begin(),   t_end.end());
        elapsed_ns[r] = static_cast<double>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(wall_end - wall_start).count());
    }

    std::sort(elapsed_ns.begin(), elapsed_ns.end());
    double med_ns = elapsed_ns[n_runs / 2];
    long total_ops = static_cast<long>(n_threads) * ops_per_thread;
    return total_ops / (med_ns / 1e9);
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

int main() {
    constexpr int      OPS_PER_THREAD = 200'000;
    constexpr int      N_RUNS         = 3;
    constexpr uint64_t SEED           = 42;

    const int max_threads = static_cast<int>(std::thread::hardware_concurrency());
    const std::vector<int> thread_counts = [&] {
        std::vector<int> v;
        for (int t = 1; t <= max_threads; t *= 2)
            v.push_back(t);
        return v;
    }();

    std::cout << std::fixed << std::setprecision(2);
    std::cout << "=== Multithreaded order book benchmark: optimized vs naive ===\n";
    std::cout << "ops/thread: " << OPS_PER_THREAD
              << "  (85% limit inserts, 15% cancels)  |  runs: " << N_RUNS << "\n\n";

    // column widths
    std::cout << std::setw(9)  << "threads"
              << std::setw(20) << "optimized ops/s"
              << std::setw(18) << "naive ops/s"
              << std::setw(14) << "opt scaling"
              << std::setw(14) << "naive scaling"
              << "\n"
              << std::string(75, '-') << "\n";

    double opt_base  = 0.0;
    double naive_base = 0.0;

    for (int t : thread_counts) {
        double opt_tput   = run_mt<OrderBook>     (t, OPS_PER_THREAD, N_RUNS, SEED);
        double naive_tput = run_mt<NaiveOrderBook>(t, OPS_PER_THREAD, N_RUNS, SEED + 500);

        if (t == 1) { opt_base = opt_tput; naive_base = naive_tput; }

        double opt_scale   = opt_tput   / opt_base;
        double naive_scale = naive_tput / naive_base;

        std::cout << std::setw(9)  << t
                  << std::setw(20) << static_cast<long>(opt_tput)
                  << std::setw(18) << static_cast<long>(naive_tput)
                  << std::setw(13) << opt_scale   << "x"
                  << std::setw(13) << naive_scale << "x"
                  << "\n";
    }

    std::cout << "\n── analysis ────────────────────────────────────────────────────────────\n";
    std::cout << "opt scaling: measures fine-grained locking benefit (per-level spinlocks\n";
    std::cout << "             allow concurrent access to disjoint price levels).\n";
    std::cout << "naive scaling: measures single-mutex contention bottleneck.\n";
    std::cout << "crossover: thread count where optimized first overtakes naive total ops/s.\n";
}
