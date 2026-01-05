#pragma once

#include <string>
#include "Order.h"

struct Trade {
    double price;
    int quantity;
    OrderSide side;
    double timestamp;

    Trade(double p, int q, OrderSide s, double t) 
        : price(p), quantity(q), side(s), timestamp(t) {}
};