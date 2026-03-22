#pragma once
#include <vector>

struct OptionChainConfig {
    std::vector<double> expiries_days;
    std::vector<double> moneyness;

    double risk_free_rate            = 0.01;
    double dividend_yield            = 0.0;
    double vol_ewma_halflife         = 120.0;
    int    min_history_samples       = 10;
    double min_vol                   = 1e-4;
    double max_vol                   = 1.0;
    int    max_chain_points          = -1;   // -1 = unlimited
    double smile_slope               = -0.15;
    double smile_convexity           = 0.10;
    double term_structure_slope      = 0.02;
    double term_structure_pivot_days = 30.0;
    double vol_noise_std             = 0.0;
    double price_noise_std           = 0.0;

    // Initialises expiries_days and moneyness to defaults.
    OptionChainConfig();
};

struct MarketSimConfig {
    double mu             = 0.0;
    double sigma          = 3.5e-5;
    double jump_intensity = 2e-7;
    double jump_mu        = 0.0;
    double jump_sigma     = 0.02;
    double dt             = 1.0;          // seconds per step

    double trade_intensity   = 1.0;
    double tick_size         = 0.01;
    int    typical_trade_qty = 100;
    double quoted_spread_bps = 5.0;

    OptionChainConfig option_chain;

    MarketSimConfig();
};
