// bench.cpp — order book throughput benchmark: optimized vs naive
//
// Build (from ob/build):
//   cmake .. && cmake --build . --target bench
// Run:
//   ./bench

#include "OrderBook.h"
#include "Order.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <deque>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <random>
#include <vector>

// ─────────────────────────────────────────────────────────────────────────────
// Naive order book
//   - std::map for price levels (pointer-chasing, O(log n) lookup)
//   - std::deque per level (heap-allocated nodes)
//   - std::mutex (syscall-based lock)
//   - raw new / delete for order allocation
// ─────────────────────────────────────────────────────────────────────────────

struct NaiveOrder {
    int       id;
    double    price;
    int       qty;
    OrderSide side;
};

class NaiveOrderBook {
public:
    // best bid first (descending), best ask first (ascending)
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
            // match against resting asks
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
            // match against resting bids
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
// Workload
//   Prices spread over [10.00, 990.00] → ~98 000 tick-sized levels.
//   At 500k ops / 2 sides / 98k levels ≈ 2.5 live orders per level on average,
//   well within the optimized book's ring-buffer capacity of 64 per level.
//   85% limit inserts, 15% cancels of previously inserted orders.
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
// Timing
// ─────────────────────────────────────────────────────────────────────────────

using Clock = std::chrono::steady_clock;

template<typename F>
static double run_ns(F&& f) {
    auto t0 = Clock::now();
    f();
    auto t1 = Clock::now();
    return static_cast<double>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count());
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

int main() {
    constexpr int      N_WARMUP = 50'000;
    constexpr int      N_BENCH  = 500'000;
    constexpr int      N_RUNS   = 5;
    constexpr uint64_t SEED     = 42;

    std::cout << std::fixed << std::setprecision(2);
    std::cout << "=== Order book throughput benchmark: optimized vs naive ===\n";
    std::cout << "ops per run: " << N_BENCH
              << "  (85% limit inserts, 15% cancels)  |  runs: " << N_RUNS << "\n\n";

    // Pre-generate workloads (one per run so we don't pathologically hit the
    // same price levels every time, but both books always see identical ops).
    std::vector<std::vector<Op>> workloads(N_RUNS);
    for (int r = 0; r < N_RUNS; ++r)
        workloads[r] = make_workload(N_BENCH, SEED + r);

    auto warmup_ops = make_workload(N_WARMUP, 9999);

    // ── warm up ────────────────────────────────────────────────────────────
    {
        OrderBook ob;
        for (const auto& op : warmup_ops) {
            if (op.type == Op::INSERT)
                ob.insertOrder(Order(op.id, op.price, op.qty, op.side, 0.0));
            else
                ob.cancelOrder(op.id, op.price);
        }
    }
    {
        NaiveOrderBook nb;
        for (const auto& op : warmup_ops) {
            if (op.type == Op::INSERT) nb.insert(op.id, op.price, op.qty, op.side);
            else                       nb.cancel(op.id, op.price, op.side);
        }
    }

    // ── timed runs ─────────────────────────────────────────────────────────
    std::vector<double> opt_ns(N_RUNS), naive_ns(N_RUNS);

    std::cout << std::setw(6)  << "run"
              << std::setw(18) << "optimized ops/s"
              << std::setw(18) << "naive ops/s"
              << std::setw(12) << "speedup"
              << "\n"
              << std::string(54, '-') << "\n";

    for (int r = 0; r < N_RUNS; ++r) {
        const auto& ops = workloads[r];

        opt_ns[r] = run_ns([&] {
            OrderBook ob;
            for (const auto& op : ops) {
                if (op.type == Op::INSERT)
                    ob.insertOrder(Order(op.id, op.price, op.qty, op.side, 0.0));
                else
                    ob.cancelOrder(op.id, op.price);
            }
        });

        naive_ns[r] = run_ns([&] {
            NaiveOrderBook nb;
            for (const auto& op : ops) {
                if (op.type == Op::INSERT) nb.insert(op.id, op.price, op.qty, op.side);
                else                       nb.cancel(op.id, op.price, op.side);
            }
        });

        long opt_ops   = static_cast<long>(N_BENCH / (opt_ns[r]   / 1e9));
        long naive_ops = static_cast<long>(N_BENCH / (naive_ns[r]  / 1e9));
        double ratio   = naive_ns[r] / opt_ns[r];

        std::cout << std::setw(6)  << (r + 1)
                  << std::setw(18) << opt_ops
                  << std::setw(18) << naive_ops
                  << std::setw(11) << ratio << "x"
                  << "\n";
    }

    // ── median summary ─────────────────────────────────────────────────────
    auto median = [](std::vector<double> v) {
        std::sort(v.begin(), v.end());
        return v[v.size() / 2];
    };

    double med_opt   = median(opt_ns);
    double med_naive = median(naive_ns);
    double ratio     = med_naive / med_opt;

    long opt_tput   = static_cast<long>(N_BENCH / (med_opt   / 1e9));
    long naive_tput = static_cast<long>(N_BENCH / (med_naive / 1e9));

    std::cout << "\n── median ───────────────────────────────────────────────\n";
    std::cout << "optimized : " << std::setw(12) << opt_tput   << " ops/s  "
              << "(" << static_cast<long>(med_opt   / N_BENCH) << " ns/op)\n";
    std::cout << "naive     : " << std::setw(12) << naive_tput << " ops/s  "
              << "(" << static_cast<long>(med_naive / N_BENCH) << " ns/op)\n";
    std::cout << "speedup   : " << ratio << "x\n";
}
