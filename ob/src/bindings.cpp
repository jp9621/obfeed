#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "OrderBook.h"
#include "Order.h"
#include "Trade.h"
#include "PriceLevel.h"

namespace py = pybind11;

PYBIND11_MODULE(ob, m) {
    m.doc() = "Orderbook simulator module";

    py::enum_<OrderSide>(m, "OrderSide")
        .value("Buy", OrderSide::Buy)
        .value("Sell", OrderSide::Sell);

    py::class_<Order>(m, "Order")
        .def(py::init<int, double, int, OrderSide, double>(),
             py::arg("id"),
             py::arg("price"),
             py::arg("quantity"),
             py::arg("side"),
             py::arg("timestamp"))
        .def("getId", &Order::getId)
        .def("getPrice", &Order::getPrice)
        .def("getQuantity", &Order::getQuantity)
        .def("getSide", &Order::getSide)
        .def("getTimeStamp", &Order::getTimeStamp);

    py::class_<Trade>(m, "Trade")
        .def(py::init<double, int, OrderSide, double>())
        .def_readwrite("price", &Trade::price)
        .def_readwrite("quantity", &Trade::quantity)
        .def_readwrite("side", &Trade::side)
        .def_readwrite("timestamp", &Trade::timestamp);

    py::class_<PriceLevel>(m, "PriceLevel")
        .def(py::init<double>())
        .def("addOrder", &PriceLevel::addOrder)
        .def("removeOrder", &PriceLevel::removeOrder)
        .def("getBidQuantity", &PriceLevel::getBidQuantity)
        .def("getAskQuantity", &PriceLevel::getAskQuantity)
        .def("getPrice", &PriceLevel::getPrice)
        .def("isEmpty", &PriceLevel::isEmpty)
        .def("processMatching", &PriceLevel::processMatching)
        .def("getOrders", &PriceLevel::getOrders);

    py::class_<OrderBook>(m, "OrderBook")
        .def(py::init<>())
        .def("insertOrder", &OrderBook::insertOrder)
        .def("cancelOrder", &OrderBook::cancelOrder, py::arg("orderId"), py::arg("price"))
        .def("matchOrder", &OrderBook::matchOrder, py::arg("side"), py::arg("price"), py::arg("quantity"), py::arg("timestamp"))
        .def("getPriceLevel", &OrderBook::getPriceLevel, py::arg("price"), py::return_value_policy::reference)
        .def("getSortedLevels", &OrderBook::getSortedLevels, py::return_value_policy::reference)
        .def("updatePosition", &OrderBook::updatePosition)
        .def("getUnrealizedPnL", &OrderBook::getUnrealizedPnL)
        .def("getBestBid", &OrderBook::getBestBid)
        .def("getBestAsk", &OrderBook::getBestAsk);
}
