#include "PriceLevel.h"
#include "Trade.h"
#include <algorithm>
#include <mutex>

PriceLevel::PriceLevel(double price) {
    _price = price;
    _bidQuantity = 0;
    _askQuantity = 0;
}

void PriceLevel::addOrder(const Order& order) {
    std::unique_lock<std::shared_mutex> lock(_mtx);
    
    if (order.getSide() == OrderSide::Buy) {
        _bids.push_back(order);
        _bidQuantity += order.getQuantity();
    } else {
        _asks.push_back(order);
        _askQuantity += order.getQuantity();
    }
    
    matchOrders(order.getTimeStamp());
}

void PriceLevel::matchOrders(double timestamp) {
    while (!_bids.empty() && !_asks.empty()) {
        Order& bid = _bids.front();
        Order& ask = _asks.front();
        
        int matchQty = std::min(bid.getQuantity(), ask.getQuantity());
        
        _bidQuantity -= matchQty;
        _askQuantity -= matchQty;
        
        _trades.emplace_back(_price, matchQty, OrderSide::Buy, timestamp);
        
        if (bid.getQuantity() == matchQty) {
            _bids.pop_front();
        } else {
            bid.setQuantity(bid.getQuantity() - matchQty);
        }
        
        if (ask.getQuantity() == matchQty) {
            _asks.pop_front();
        } else {
            ask.setQuantity(ask.getQuantity() - matchQty);
        }
    }
}

bool PriceLevel::removeOrder(int orderId) {
    std::unique_lock<std::shared_mutex> lock(_mtx);
    
    for (auto it = _bids.begin(); it != _bids.end(); ++it) {
        if (it->getId() == orderId) {
            _bidQuantity -= it->getQuantity();
            _bids.erase(it);
            return true;
        }
    }
    
    for (auto it = _asks.begin(); it != _asks.end(); ++it) {
        if (it->getId() == orderId) {
            _askQuantity -= it->getQuantity();
            _asks.erase(it);
            return true;
        }
    }
    
    return false;
}

std::vector<Trade> PriceLevel::processMatching(int incomingQuantity, double timestamp, OrderSide side) {
    std::unique_lock<std::shared_mutex> lock(_mtx);
    std::vector<Trade> trades;
    
    std::deque<Order>& orders = (side == OrderSide::Buy) ? _asks : _bids;
    int& oppositeQty = (side == OrderSide::Buy) ? _askQuantity : _bidQuantity;
    
    int matchQty = std::min(incomingQuantity, oppositeQty);
    if (matchQty > 0) {
        trades.emplace_back(_price, matchQty, side, timestamp);
        
        oppositeQty -= matchQty;
        
        int remainingMatch = matchQty;
        while (remainingMatch > 0 && !orders.empty()) {
            Order& order = orders.front();
            int orderFill = std::min(remainingMatch, order.getQuantity());
            
            if (orderFill == order.getQuantity()) {
                orders.pop_front();
            } else {
                order.setQuantity(order.getQuantity() - orderFill);
            }
            remainingMatch -= orderFill;
        }
    }
    
    return trades;
}

int PriceLevel::getBidQuantity() const {
    std::shared_lock<std::shared_mutex> lock(_mtx);
    return _bidQuantity;
}

int PriceLevel::getAskQuantity() const {
    std::shared_lock<std::shared_mutex> lock(_mtx);
    return _askQuantity;
}

double PriceLevel::getPrice() const {
    std::shared_lock<std::shared_mutex> lock(_mtx);
    return _price;
}

bool PriceLevel::isEmpty() const {
    std::shared_lock<std::shared_mutex> lock(_mtx);
    return _bids.empty() && _asks.empty();
}

void PriceLevel::printOrders() const {
    std::shared_lock<std::shared_mutex> lock(_mtx);
    std::cout << "Price Level: " << _price << " | Total Quantity: " << _bidQuantity + _askQuantity << "\n";
    for (const auto &order : _bids) {
        std::cout << "  Order ID: " << order.getId() << ", Qty: " << order.getQuantity() << " (Bid)\n";
    }
    for (const auto &order : _asks) {
        std::cout << "  Order ID: " << order.getId() << ", Qty: " << order.getQuantity() << " (Ask)\n";
    }
}

std::vector<Order> PriceLevel::getOrders(OrderSide side) const {
    std::shared_lock<std::shared_mutex> lock(_mtx);
    std::vector<Order> orders;
    
    const std::deque<Order>& sourceOrders = (side == OrderSide::Buy) ? _bids : _asks;
    orders.reserve(sourceOrders.size());
    orders.insert(orders.end(), sourceOrders.begin(), sourceOrders.end());
    
    return orders;
}
