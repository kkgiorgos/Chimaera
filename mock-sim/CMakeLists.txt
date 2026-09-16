cmake_minimum_required(VERSION 3.21)
project(MockSim LANGUAGES CXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "The mock socket transports currently require Linux")
endif()

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(MOCK_SIM_BUILD_EXAMPLES "Build host and guest example programs" ${PROJECT_IS_TOP_LEVEL})

add_library(transport INTERFACE)
add_library(MockSim::transport ALIAS transport)
target_compile_features(transport INTERFACE cxx_std_20)
target_include_directories(transport INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/include)

add_library(mock_host src/mock_host_transport.cpp)
add_library(MockSim::mock_host ALIAS mock_host)
target_link_libraries(mock_host PUBLIC MockSim::transport)

add_library(mock_guest src/mock_guest_transport.cpp)
add_library(MockSim::mock_guest ALIAS mock_guest)
target_link_libraries(mock_guest PUBLIC MockSim::transport)

if(MOCK_SIM_BUILD_EXAMPLES)
    add_executable(mock_sim_host examples/host.cpp)
    target_link_libraries(mock_sim_host PRIVATE MockSim::mock_host)
    add_executable(mock_sim_guest examples/guest.cpp)
    target_link_libraries(mock_sim_guest PRIVATE MockSim::mock_guest)
endif()

