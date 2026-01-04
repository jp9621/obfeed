#ifndef ORDERBOOK_H
#define ORDERBOOK_H

#include "PriceLevel.h"
#include "Order.h"
#include "Trade.h"

#include <map>
#include <unordered_map>
#include <shared_mutex>

class OrderBook {
    public:
        OrderBook();
        ~OrderBook();

        void insertOrder(const Order& order);
        bool cancelOrder(int orderId, double price);
        std::vector<Trade> matchOrder(OrderSide side, double price, int quantity, double timestamp);

        void updatePosition(const Trade& trade);
        double getUnrealizedPnL(double currentPrice) const;
        double getBestBid() const;
        double getBestAsk() const;

        PriceLevel* getPriceLevel(double price);
        std::map<double, PriceLevel*>& getSortedLevels();
    
    private:
        std::map<double, PriceLevel*> _sortedLevels;
        std::unordered_map<double, PriceLevel*> _priceMap;
        mutable std::shared_mutex _bookmtx;

        int _position = 0;
        double _realizedPnL = 0.0;
        double _totalCost = 0.0;
};

#endif