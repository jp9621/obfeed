#ifndef PRICELEVEL_H
#define PRICELEVEL_H

#include "Order.h"
#include "Trade.h"
#include <deque>
#include <mutex>
#include <shared_mutex>
#include <iostream>
#include <vector>

class PriceLevel {
    public:
        PriceLevel(double price);

        void addOrder(const Order& order);
        bool removeOrder(int orderId);
        std::vector<Trade> processMatching(int quantity, double timestamp, OrderSide side);
        double getPrice() const;
        bool isEmpty() const;
        int getBidQuantity() const;
        int getAskQuantity() const;
        void printOrders() const;
        std::vector<Order> getOrders(OrderSide side) const;
    
    private:
        void matchOrders(double timestamp);
        
        double _price;
        int _bidQuantity;
        int _askQuantity;
        std::deque<Order> _bids;
        std::deque<Order> _asks;
        std::vector<Trade> _trades;

        mutable std::shared_mutex _mtx;
};

#endif