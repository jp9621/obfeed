#include "Simulator.h"
#include <cstdio>

static void print_tick(const MarketTickEvent& t) {
    std::printf("  bid=%.4f  mid=%.4f  ask=%.4f  sizes=%d/%d\n",
                t.bid, t.mid, t.ask, t.bid_size, t.ask_size);
}

int main() {
    MarketSimulator sim(100.0, MarketSimConfig{}, /*seed=*/42ULL);

    std::printf("Running 20 simulation steps\n");
    std::printf("----------------------------\n");

    for (int i = 0; i < 20; ++i) {
        SimStep s = sim.step();

        std::printf("step %2d | price=%.4f | trades=%zu | options=%zu\n",
                    i + 1, sim.current_price(),
                    s.trades.size(), s.options.size());
        print_tick(s.tick);

        if (!s.trades.empty()) {
            for (const auto& tr : s.trades)
                std::printf("         trade  %s  %.4f x %d\n",
                            tr.is_buy ? "BUY " : "SELL", tr.price, tr.qty);
        }
    }

    return 0;
}
