cmake_minimum_required(VERSION 3.21)
project(Gem5Transport LANGUAGES CXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "The gem5 transport requires Linux")
endif()

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
add_library(chimaera_transport INTERFACE)
add_library(chimaera::transport ALIAS chimaera_transport)
target_compile_features(chimaera_transport INTERFACE cxx_std_20)
target_include_directories(chimaera_transport INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/include)

option(GEM5_TRANSPORT_BUILD_GUEST "Build guest library (requires custom libm5)" ON)
option(GEM5_TRANSPORT_BUILD_EXAMPLES "Build interactive examples" ${PROJECT_IS_TOP_LEVEL})
set(GEM5_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../gem5" CACHE PATH "Custom gem5 source tree")

add_library(gem5_host src/host_transport.cpp)
add_library(chimaera::host ALIAS gem5_host)
target_link_libraries(gem5_host PUBLIC chimaera::transport)
target_include_directories(gem5_host PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

if(GEM5_TRANSPORT_BUILD_GUEST)
    find_path(GEM5_OP_INCLUDE gem5/m5ops.h HINTS "${GEM5_ROOT}/include" REQUIRED)
    find_path(GEM5_MMAP_INCLUDE m5_mmap.h HINTS "${GEM5_ROOT}/util/m5/src" REQUIRED)
    find_library(GEM5_M5_LIBRARY m5 HINTS "${GEM5_ROOT}/util/m5/build/x86/out" REQUIRED)
    add_library(gem5_guest src/guest_transport.cpp)
    add_library(chimaera::guest ALIAS gem5_guest)
    target_include_directories(gem5_guest PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
        PRIVATE ${GEM5_OP_INCLUDE} ${GEM5_MMAP_INCLUDE})
    target_link_libraries(gem5_guest PUBLIC chimaera::transport PRIVATE ${GEM5_M5_LIBRARY})
    target_link_options(gem5_guest INTERFACE -no-pie)
endif()

if(GEM5_TRANSPORT_BUILD_EXAMPLES)
    add_executable(gem5_transport_host examples/host.cpp)
    target_link_libraries(gem5_transport_host PRIVATE chimaera::host)
    if(GEM5_TRANSPORT_BUILD_GUEST)
        add_executable(gem5_transport_guest examples/guest.cpp)
        target_link_libraries(gem5_transport_guest PRIVATE chimaera::guest)
        target_include_directories(gem5_transport_guest PRIVATE ${GEM5_MMAP_INCLUDE})
    endif()
endif()

