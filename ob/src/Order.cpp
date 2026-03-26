#include <Order.h>

Order::Order(int id, double price, int quantity, OrderSide side, double time) {
    _orderId = id;
    _price = price;
    _quantity = quantity;
    _side = side;
    _timestamp = time;
}

int Order::getId() const {
    return _orderId;
}

double Order::getPrice() const {
    return _price;
}

int Order::getQuantity() const {
    return _quantity;
}

OrderSide Order::getSide() const {
    return _side;
}

double Order::getTimeStamp() const {
    return _timestamp;
}

void Order::setQuantity(int newQuantity) {
    _quantity = newQuantity;
}