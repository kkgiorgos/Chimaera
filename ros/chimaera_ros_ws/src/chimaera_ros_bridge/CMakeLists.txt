cmake_minimum_required(VERSION 3.22)
project(chimaera_ros_bridge LANGUAGES CXX)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(chimaera_dds_transport REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(JSON_C REQUIRED IMPORTED_TARGET json-c)
set(GEM5_TRANSPORT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../../../gem5-transport"
    CACHE PATH "Chimaera gem5-transport source directory")
option(BUILD_GUEST_BRIDGE "Build the libm5 guest node" ON)
set(GEM5_TRANSPORT_BUILD_GUEST ${BUILD_GUEST_BRIDGE} CACHE BOOL "" FORCE)
set(GEM5_TRANSPORT_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory("${GEM5_TRANSPORT_ROOT}" "${CMAKE_CURRENT_BINARY_DIR}/gem5-transport")

add_library(bridge INTERFACE)
target_include_directories(bridge INTERFACE include)
target_link_libraries(bridge INTERFACE chimaera::transport PkgConfig::JSON_C
  chimaera_dds_transport::chimaera_dds_transport)
add_executable(validate_routes src/validate_routes.cpp)
target_link_libraries(validate_routes PRIVATE bridge)
ament_target_dependencies(validate_routes PUBLIC rclcpp)
install(TARGETS validate_routes DESTINATION lib/${PROJECT_NAME})
foreach(side host guest)
  if(side STREQUAL "guest" AND NOT BUILD_GUEST_BRIDGE)
    continue()
  endif()
  add_executable(${side}_bridge src/${side}_bridge.cpp)
  target_link_libraries(${side}_bridge PRIVATE bridge chimaera::${side}_controller)
  target_compile_options(${side}_bridge PRIVATE -Wall -Wextra -Wpedantic)
  ament_target_dependencies(${side}_bridge PUBLIC rclcpp)
  install(TARGETS ${side}_bridge DESTINATION lib/${PROJECT_NAME})
endforeach()
option(BUILD_GAZEBO_BRIDGE "Build synchronized Gazebo Fortress host integration" OFF)
if(BUILD_GAZEBO_BRIDGE)
  find_package(ignition-transport11 REQUIRED)
  find_package(ignition-msgs8 REQUIRED)
  add_executable(gazebo_host_bridge src/gazebo_host_bridge.cpp)
  target_link_libraries(gazebo_host_bridge PRIVATE bridge chimaera::host_controller
    ignition-transport11::core ignition-msgs8::core)
  ament_target_dependencies(gazebo_host_bridge PUBLIC rclcpp)
  target_compile_options(gazebo_host_bridge PRIVATE -Wall -Wextra -Wpedantic)
  install(TARGETS gazebo_host_bridge DESTINATION lib/${PROJECT_NAME})
endif()
option(BUILD_LATENCY_BENCHMARK "Build the synthetic Gazebo/Chimaera latency benchmark" OFF)
if(BUILD_LATENCY_BENCHMARK)
  find_package(std_msgs REQUIRED)
  find_package(sensor_msgs REQUIRED)
  find_package(rosgraph_msgs REQUIRED)
  find_package(ignition-gazebo6 REQUIRED)
  find_package(ignition-transport11 REQUIRED)
  find_package(ignition-msgs8 REQUIRED)
  add_library(chimaera_latency_clock SHARED benchmark/clock_plugin.cpp)
  target_compile_features(chimaera_latency_clock PRIVATE cxx_std_17)
  target_link_libraries(chimaera_latency_clock PRIVATE ignition-gazebo6::core)
  add_executable(latency_endpoint benchmark/endpoint.cpp)
  target_compile_features(latency_endpoint PRIVATE cxx_std_17)
  ament_target_dependencies(latency_endpoint PUBLIC rclcpp std_msgs sensor_msgs rosgraph_msgs)
  target_link_libraries(latency_endpoint PRIVATE ${CMAKE_DL_LIBS})
  add_executable(latency_driver benchmark/driver.cpp)
  target_link_libraries(latency_driver PRIVATE bridge chimaera::host_controller
    ignition-transport11::core ignition-msgs8::core)
  ament_target_dependencies(latency_driver PUBLIC rclcpp)
  foreach(target latency_endpoint latency_driver chimaera_latency_clock)
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
  endforeach()
  install(TARGETS latency_endpoint latency_driver DESTINATION lib/${PROJECT_NAME})
  install(TARGETS chimaera_latency_clock LIBRARY DESTINATION lib)
  install(PROGRAMS scripts/chimaera_latency DESTINATION lib/${PROJECT_NAME})
  install(FILES benchmark/gem5_latency.py DESTINATION share/${PROJECT_NAME}/config)
  install(FILES benchmark/latency_capabilities.json DESTINATION share/${PROJECT_NAME}/config)
  install(DIRECTORY latency_web DESTINATION share/${PROJECT_NAME})
endif()
if(BUILD_GUEST_BRIDGE)
  add_library(chimaera_gem5_clock SHARED src/gem5_clock.cpp)
  target_include_directories(chimaera_gem5_clock PRIVATE ${GEM5_OP_INCLUDE})
  target_link_libraries(chimaera_gem5_clock PRIVATE ${GEM5_M5_LIBRARY})
  # libm5.a's address stubs use absolute relocations. Build its supported PIC
  # variant for the clock loaded into robot processes during KVM readiness.
  enable_language(C ASM)
  add_library(chimaera_gem5_clock_addr SHARED src/gem5_clock.cpp
    ${GEM5_MMAP_INCLUDE}/m5_mmap.c ${GEM5_MMAP_INCLUDE}/abi/x86/m5op_addr.S)
  target_compile_definitions(chimaera_gem5_clock_addr PRIVATE CHIMAERA_CLOCK_ADDRESS M5OP_PIC)
  target_include_directories(chimaera_gem5_clock_addr PRIVATE ${GEM5_OP_INCLUDE} ${GEM5_MMAP_INCLUDE})
  install(TARGETS chimaera_gem5_clock chimaera_gem5_clock_addr LIBRARY DESTINATION lib)
  target_include_directories(guest_bridge PRIVATE ${GEM5_OP_INCLUDE} ${GEM5_MMAP_INCLUDE})
  target_link_libraries(guest_bridge PRIVATE ${GEM5_M5_LIBRARY})
endif()
install(DIRECTORY launch config DESTINATION share/${PROJECT_NAME})
find_package(ament_cmake_python REQUIRED)
ament_python_install_package(chimaera_ros)
install(PROGRAMS scripts/chimaera_ros scripts/deploy_image.sh DESTINATION lib/${PROJECT_NAME})
if(BUILD_TESTING)
  find_package(std_msgs REQUIRED)
  enable_testing()
  add_executable(serialized_view_test test/serialized_view_test.cpp)
  add_executable(completion_waitable_test test/completion_waitable_test.cpp)
  target_link_libraries(completion_waitable_test PRIVATE bridge)
  ament_target_dependencies(completion_waitable_test PUBLIC rclcpp)
  add_test(NAME completion_waitable_test COMMAND completion_waitable_test)
  set_tests_properties(completion_waitable_test PROPERTIES TIMEOUT 20 ENVIRONMENT
    "ROS_LOCALHOST_ONLY=1;ROS_DOMAIN_ID=193;ROS_LOG_DIR=${CMAKE_CURRENT_BINARY_DIR}/test_ros_logs")
  target_link_libraries(serialized_view_test PRIVATE bridge)
  ament_target_dependencies(serialized_view_test PUBLIC rclcpp)
  add_test(NAME serialized_view_test COMMAND serialized_view_test)
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  add_test(NAME dds_libraries_test COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s ${CMAKE_CURRENT_SOURCE_DIR}/test -p test_dds_libraries.py)
  set_tests_properties(dds_libraries_test PROPERTIES ENVIRONMENT
    "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
  add_executable(bridge_test test/bridge_test.cpp)
  target_link_libraries(bridge_test PRIVATE bridge)
  ament_target_dependencies(bridge_test PUBLIC rclcpp std_msgs)
  target_compile_definitions(bridge_test PRIVATE
    TEST_CONFIG="${CMAKE_CURRENT_SOURCE_DIR}/test/routes.json")
  add_test(NAME bridge_test COMMAND bridge_test)
  set_tests_properties(bridge_test PROPERTIES TIMEOUT 20
    ENVIRONMENT "ROS_LOCALHOST_ONLY=1;ROS_DOMAIN_ID=197;ROS_LOG_DIR=${CMAKE_CURRENT_BINARY_DIR}/test_ros_logs")
  if(BUILD_GAZEBO_BRIDGE)
    find_package(sensor_msgs REQUIRED)
    find_package(rosgraph_msgs REQUIRED)
    add_executable(sensor_bridge_test test/sensor_bridge_test.cpp)
    target_link_libraries(sensor_bridge_test PRIVATE bridge)
    target_include_directories(sensor_bridge_test PRIVATE "${GEM5_TRANSPORT_ROOT}/src")
    ament_target_dependencies(sensor_bridge_test PUBLIC rclcpp std_msgs sensor_msgs rosgraph_msgs)
    target_compile_definitions(sensor_bridge_test PRIVATE
      WALL_CONFIG="${CMAKE_CURRENT_SOURCE_DIR}/test/sensor_routes.json"
      TEST_CONFIG="${CMAKE_CURRENT_SOURCE_DIR}/test/sensor_test_routes.json")
    add_test(NAME sensor_bridge_test COMMAND sensor_bridge_test)
    add_executable(stereo_freshness_test test/stereo_freshness_test.cpp)
    target_link_libraries(stereo_freshness_test PRIVATE bridge)
    ament_target_dependencies(stereo_freshness_test PUBLIC rclcpp sensor_msgs std_msgs)
    add_test(NAME stereo_freshness_test COMMAND stereo_freshness_test)
    set_tests_properties(stereo_freshness_test PROPERTIES TIMEOUT 30
      ENVIRONMENT "ROS_LOCALHOST_ONLY=1;ROS_DOMAIN_ID=194;ROS_LOG_DIR=${CMAKE_CURRENT_BINARY_DIR}/test_ros_logs")
    set_tests_properties(sensor_bridge_test PROPERTIES TIMEOUT 30
      ENVIRONMENT "ROS_LOCALHOST_ONLY=1;ROS_DOMAIN_ID=195;ROS_LOG_DIR=${CMAKE_CURRENT_BINARY_DIR}/test_ros_logs")
    add_test(NAME timing_integration COMMAND ${Python3_EXECUTABLE}
      ${CMAKE_CURRENT_SOURCE_DIR}/test/timing_integration.py $<TARGET_FILE:gazebo_host_bridge>
      ${CMAKE_CURRENT_SOURCE_DIR}/test/sensor_routes.json)
    set_tests_properties(timing_integration PROPERTIES TIMEOUT 90 RUN_SERIAL TRUE)
  endif()
  add_test(NAME gem5_timing_test COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s ${CMAKE_CURRENT_SOURCE_DIR}/test -p test_gem5_timing.py)
  add_test(NAME manifest_test COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s ${CMAKE_CURRENT_SOURCE_DIR}/test -p test_manifest.py)
  set_tests_properties(manifest_test PROPERTIES ENVIRONMENT
    "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
  add_test(NAME latency_analysis_test COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s ${CMAKE_CURRENT_SOURCE_DIR}/test -p test_latency.py)
  set_tests_properties(latency_analysis_test PROPERTIES ENVIRONMENT
    "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
  add_test(NAME latency_dashboard_test COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s ${CMAKE_CURRENT_SOURCE_DIR}/test -p test_latency_dashboard.py)
  set_tests_properties(latency_dashboard_test PROPERTIES ENVIRONMENT
    "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
  add_test(NAME latency_traffic_test COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s ${CMAKE_CURRENT_SOURCE_DIR}/test -p test_latency_traffic.py)
  set_tests_properties(latency_traffic_test PROPERTIES ENVIRONMENT
    "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
  add_test(NAME latency_sessions_test COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s ${CMAKE_CURRENT_SOURCE_DIR}/test -p test_latency_sessions.py)
  set_tests_properties(latency_sessions_test PROPERTIES ENVIRONMENT
    "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
  add_test(NAME latency_dma_test COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s ${CMAKE_CURRENT_SOURCE_DIR}/test -p test_latency_dma.py)
  set_tests_properties(latency_dma_test PROPERTIES ENVIRONMENT
    "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
  if(BUILD_LATENCY_BENCHMARK)
    add_test(NAME latency_message_test COMMAND ${Python3_EXECUTABLE}
      ${CMAKE_CURRENT_SOURCE_DIR}/test/latency_message_integration.py $<TARGET_FILE:latency_endpoint>)
    set_tests_properties(latency_message_test PROPERTIES TIMEOUT 30 ENVIRONMENT
      "ROS_LOCALHOST_ONLY=1;ROS_DOMAIN_ID=191;ROS_LOG_DIR=${CMAKE_CURRENT_BINARY_DIR}/test_ros_logs")
  endif()
endif()
ament_package()
