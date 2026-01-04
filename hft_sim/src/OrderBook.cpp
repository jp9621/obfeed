#include "OrderBook.h"
#include "PriceLevel.h"
#include "Trade.h"
#include "Order.h"
#include <iostream>
#include <algorithm>
#include <limits>
#include <cmath>
#include <vector>

OrderBook::OrderBook() {}

OrderBook::~OrderBook() {
    for (auto& pair : _priceMap) {
        delete pair.second;
    }
}

void OrderBook::insertOrder(const Order& order) {
    if (order.getPrice() == 0.0) {
        matchOrder(order.getSide(), 0.0, order.getQuantity(), order.getTimeStamp());
        return;
    }

    if (order.getSide() == OrderSide::Sell) {
        double bestBid = getBestBid();
        if (bestBid > 0.0 && order.getPrice() <= bestBid) {
            matchOrder(OrderSide::Sell, order.getPrice(), order.getQuantity(), order.getTimeStamp());
            return;
        }
    } else {
        double bestAsk = getBestAsk();
        if (bestAsk < std::numeric_limits<double>::max() && order.getPrice() >= bestAsk) {
            matchOrder(OrderSide::Buy, order.getPrice(), order.getQuantity(), order.getTimeStamp());
            return;
        }
    }

    double price = order.getPrice();
    {
        std::unique_lock<std::shared_mutex> lock(_bookmtx);
        if (_priceMap.find(price) == _priceMap.end()) {
            PriceLevel *level = new PriceLevel(price);
            _sortedLevels.emplace(price, level);
            _priceMap.emplace(price, level);
        }
    }
    _priceMap[price]->addOrder(order);
}

bool OrderBook::cancelOrder(int orderId, double price) {
    std::unique_lock<std::shared_mutex> lock(_bookmtx);
    auto it = _priceMap.find(price);
    if (it != _priceMap.end()) {
        bool removed = it->second->removeOrder(orderId);
        if (it->second->isEmpty()) {
            _sortedLevels.erase(price);
            _priceMap.erase(price);
            delete it->second;
        }
        return removed;
    }
    return false;
}

std::vector<Trade> OrderBook::matchOrder(OrderSide side, double price, int quantity, double timestamp) {
    std::vector<Trade> allTrades;
    int remaining = quantity;
    std::unique_lock<std::shared_mutex> lock(_bookmtx);

    std::vector<std::pair<double, PriceLevel*>> levelsToMatch;
    int totalAvailableQty = 0;

    if (side == OrderSide::Buy) {
        auto it = _sortedLevels.begin();
        while (it != _sortedLevels.end() && totalAvailableQty < quantity) {
            if (price == 0.0 || it->first <= price) {
                int levelAskQty = it->second->getAskQuantity();
                if (levelAskQty > 0) {
                    levelsToMatch.push_back({it->first, it->second});
                    totalAvailableQty += levelAskQty;
                }
            } else {
                break;
            }
            ++it;
        }
    } else {
        auto it = _sortedLevels.rbegin();
        while (it != _sortedLevels.rend() && totalAvailableQty < quantity) {
            if (price == 0.0 || it->first >= price) {
                int levelBidQty = it->second->getBidQuantity();
                if (levelBidQty > 0) {
                    levelsToMatch.push_back({it->first, it->second});
                    totalAvailableQty += levelBidQty;
                }
            } else {
                break;
            }
            ++it;
        }
    }

    for (auto& [levelPrice, level] : levelsToMatch) {
        if (remaining <= 0) break;

        auto trades = level->processMatching(remaining, timestamp, side);
        for (const auto& trade : trades) {
            this->updatePosition(trade);
        }
        allTrades.insert(allTrades.end(), trades.begin(), trades.end());
        
        for (const auto& trade : trades) {
            remaining -= trade.quantity;
        }

        if (level->isEmpty()) {
            _sortedLevels.erase(levelPrice);
            _priceMap.erase(levelPrice);
            delete level;
        }
    }

    if (remaining > 0 && price > 0.0) {
        Order remainingOrder(timestamp, price, remaining, side, timestamp);
        insertOrder(remainingOrder);
    }

    return allTrades;
}

void OrderBook::updatePosition(const Trade& trade) {
    int positionDelta = trade.side == OrderSide::Buy ? trade.quantity : -trade.quantity;
    
    if ((_position > 0 && positionDelta < 0) || (_position < 0 && positionDelta > 0)) {
        double avgPrice = _totalCost / std::abs(_position);
        if (_position > 0) {
            _realizedPnL += (trade.price - avgPrice) * std::min(std::abs(positionDelta), std::abs(_position));
        } else {
            _realizedPnL += (avgPrice - trade.price) * std::min(std::abs(positionDelta), std::abs(_position));
        }
    }
    
    _position += positionDelta;
    _totalCost += positionDelta * trade.price;
    
    if ((_position > 0 && _position - positionDelta < 0) || 
        (_position < 0 && _position - positionDelta > 0)) {
        _totalCost = _position * trade.price;
    }
}

double OrderBook::getUnrealizedPnL(double currentPrice) const {
    if (_position == 0) return 0.0;
    
    double avgPrice = _totalCost / std::abs(_position);
    if (_position > 0) {
        return (currentPrice - avgPrice) * _position;
    } else {
        return (avgPrice - currentPrice) * std::abs(_position);
    }
}

double OrderBook::getBestBid() const {
    std::shared_lock<std::shared_mutex> lock(_bookmtx);
    auto it = _sortedLevels.rbegin();
    while (it != _sortedLevels.rend()) {
        if (it->second->getBidQuantity() > 0) {
            return it->first;
        }
        ++it;
    }
    return 0.0;
}

double OrderBook::getBestAsk() const {
    std::shared_lock<std::shared_mutex> lock(_bookmtx);
    auto it = _sortedLevels.begin();
    while (it != _sortedLevels.end()) {
        if (it->second->getAskQuantity() > 0) {
            return it->first;
        }
        ++it;
    }
    return std::numeric_limits<double>::max();
}

PriceLevel* OrderBook::getPriceLevel(double price) {
    std::shared_lock<std::shared_mutex> lock(_bookmtx);
    auto it = _priceMap.find(price);
    if (it != _priceMap.end()) {
        return it->second;
    }
    return nullptr;
}

std::map<double, PriceLevel*>& OrderBook::getSortedLevels() {
    return _sortedLevels;
}
