cmake_minimum_required(VERSION 3.8)
project(chimaera_bridge)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)

function(add_chimaera_bridge_node target source)
  add_executable(${target} ${source})
  target_include_directories(${target} PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>)
  target_compile_features(${target} PUBLIC c_std_99 cxx_std_17)
  ament_target_dependencies(${target} rclcpp)
endfunction()

add_chimaera_bridge_node(g2h_bridge src/g2h_bridge.cpp)
add_chimaera_bridge_node(h2g_bridge src/h2g_bridge.cpp)

install(TARGETS
  g2h_bridge
  h2g_bridge
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
