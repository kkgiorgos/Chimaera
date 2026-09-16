cmake_minimum_required(VERSION 3.8)
project(chimaera_bridge)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)

set(GEM5_ROOT "$ENV{GEM5_ROOT}" CACHE PATH "Path to the gem5 source tree")
if(NOT GEM5_ROOT)
  get_filename_component(GEM5_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../../gem5" ABSOLUTE)
endif()

set(GEM5_M5_ARCH "x86" CACHE STRING "gem5 util/m5 build architecture")

find_path(GEM5_INCLUDE_DIR
  NAMES gem5/m5ops.h
  HINTS "${GEM5_ROOT}/include"
  REQUIRED)

find_path(GEM5_M5_INCLUDE_DIR
  NAMES m5_mmap.h
  HINTS "${GEM5_ROOT}/util/m5/src"
  REQUIRED)

find_library(GEM5_M5_LIBRARY
  NAMES m5
  HINTS "${GEM5_ROOT}/util/m5/build/${GEM5_M5_ARCH}/out"
  REQUIRED)

add_library(gem5_m5 STATIC IMPORTED)
set_target_properties(gem5_m5 PROPERTIES
  IMPORTED_LOCATION "${GEM5_M5_LIBRARY}"
  INTERFACE_INCLUDE_DIRECTORIES "${GEM5_INCLUDE_DIR};${GEM5_M5_INCLUDE_DIR}")

function(add_chimaera_bridge_node target source)
  add_executable(${target} ${source})
  target_include_directories(${target} PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>)
  target_compile_features(${target} PUBLIC c_std_99 cxx_std_17)
  ament_target_dependencies(${target} rclcpp std_msgs)
  target_link_libraries(${target} gem5_m5)
  target_link_options(${target} PRIVATE -no-pie)
endfunction()

add_chimaera_bridge_node(bridge src/bridge.cpp)

install(TARGETS
  bridge
  DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
