cmake_minimum_required(VERSION 3.15)
project(ob LANGUAGES CXX)

# 1) Turn on C++17
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# 2) Only set CMP0148 policy if CMake version supports it
if(POLICY CMP0148)
    cmake_policy(SET CMP0148 NEW)
endif()

# 3) Find Python interpreter + headers + library
find_package(Python REQUIRED COMPONENTS Interpreter Development)
# Now Python_EXECUTABLE, Python_INCLUDE_DIRS, Python_LIBRARIES are available

# 4) Fetch pybind11
include(FetchContent)
FetchContent_Declare(
  pybind11
  GIT_REPOSITORY https://github.com/pybind/pybind11.git
  GIT_TAG        v2.11.1
)
FetchContent_MakeAvailable(pybind11)

# 5) Build your engine as a static library
add_library(ob_engine
    src/OrderBook.cpp
    src/PriceLevel.cpp
    src/Order.cpp
)
target_include_directories(ob_engine PUBLIC include)
find_package(Threads REQUIRED)
target_link_libraries(ob_engine PUBLIC Threads::Threads)

# 6) Expose Python module
pybind11_add_module(ob
    src/bindings.cpp
)
target_link_libraries(ob PRIVATE ob_engine)

# 7) Ensure the Python include path is passed
target_include_directories(ob PRIVATE ${Python_INCLUDE_DIRS})
target_link_libraries(ob PRIVATE ${Python_LIBRARIES})
