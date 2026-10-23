cmake_minimum_required(VERSION 3.22)
project(chimaera_head_of_line_probe LANGUAGES CXX)
find_package(fastrtps 2.6 REQUIRED)
find_package(Threads REQUIRED)
get_filename_component(REPOSITORY "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
set(DDS_SOURCE "${REPOSITORY}/ros/chimaera_ros_ws/src/chimaera_dds_transport")
add_executable(head_of_line_probe probe.cpp "${DDS_SOURCE}/src/router.cpp")
target_compile_features(head_of_line_probe PRIVATE cxx_std_20)
target_compile_options(head_of_line_probe PRIVATE -Wall -Wextra -Wpedantic)
target_include_directories(head_of_line_probe PRIVATE
    "${DDS_SOURCE}/include" "${DDS_SOURCE}/src"
    "${REPOSITORY}/gem5-transport/include" "${REPOSITORY}/gem5-transport/src"
    "${REPOSITORY}/gem5/include")
target_link_libraries(head_of_line_probe PRIVATE fastrtps Threads::Threads)
