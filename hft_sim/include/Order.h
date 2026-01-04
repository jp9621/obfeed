#ifndef ORDER_H
#define ORDER_H

#include <chrono>

enum class OrderSide {
    Buy,
    Sell
};

class Order {
    public:
        Order(int id, double price, int quantity, OrderSide side, double time);
        int getId() const;
        double getPrice() const;
        int getQuantity() const;
        OrderSide getSide() const;
        double getTimeStamp() const;

        void setQuantity(int newQuantity);

    private:
        int _orderId;
        double _price;
        int _quantity;
        OrderSide _side;
        double _timestamp;
};

#endif