cmake_minimum_required(VERSION 3.15)
project(ob LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Threads REQUIRED)

# Core order-book + simulator library
add_library(ob_engine
    src/OrderBook.cpp
    src/PriceLevel.cpp
    src/Order.cpp
    src/Simulator.cpp
)
target_include_directories(ob_engine PUBLIC include)
target_link_libraries(ob_engine PUBLIC Threads::Threads)

# Standalone executable
add_executable(ob_main main.cpp)
target_link_libraries(ob_main PRIVATE ob_engine)
