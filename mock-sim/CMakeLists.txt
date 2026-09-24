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

add_library(controller INTERFACE)
add_library(MockSim::controller ALIAS controller)
target_link_libraries(controller INTERFACE MockSim::transport)

add_library(mock_timing src/mock_timing_controller.cpp)
add_library(MockSim::mock_timing ALIAS mock_timing)
target_link_libraries(mock_timing PUBLIC MockSim::controller)

add_library(mock_host_controller src/mock_host_controller.cpp)
add_library(MockSim::mock_host_controller ALIAS mock_host_controller)
target_link_libraries(mock_host_controller PUBLIC MockSim::controller PRIVATE MockSim::mock_host)

add_library(mock_guest_controller src/mock_guest_controller.cpp)
add_library(MockSim::mock_guest_controller ALIAS mock_guest_controller)
target_link_libraries(mock_guest_controller PUBLIC MockSim::controller PRIVATE MockSim::mock_guest)

if(MOCK_SIM_BUILD_EXAMPLES)
    if(NOT TARGET chimaera::queue_manager)
        add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../queue-manager
                         ${CMAKE_CURRENT_BINARY_DIR}/queue-manager)
    endif()
    add_executable(mock_controller_host examples/controller_host.cpp)
    target_link_libraries(mock_controller_host PRIVATE MockSim::mock_host_controller MockSim::mock_timing chimaera::queue_manager)
    add_executable(mock_controller_guest examples/controller_guest.cpp)
    target_link_libraries(mock_controller_guest PRIVATE MockSim::mock_guest_controller chimaera::queue_manager)
    add_executable(mock_sim_host examples/host.cpp)
    target_link_libraries(mock_sim_host PRIVATE MockSim::mock_host)
    add_executable(mock_sim_guest examples/guest.cpp)
    target_link_libraries(mock_sim_guest PRIVATE MockSim::mock_guest)
endif()


include(CTest)
if(BUILD_TESTING AND MOCK_SIM_BUILD_EXAMPLES)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME queue_controller_integration
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/queue_controller_integration.py
                $<TARGET_FILE:mock_controller_host> $<TARGET_FILE:mock_controller_guest>)
    set_tests_properties(queue_controller_integration PROPERTIES TIMEOUT 30)
endif()
