ARG SDK_IMAGE=chimaera:jammy-humble-fortress
FROM ${SDK_IMAGE}
USER root
ARG BUILD_JOBS=3
COPY middleware/prepare_rmw.py /opt/chimaera/scripts/prepare-rmw.py
RUN python3 /opt/chimaera/scripts/prepare-rmw.py --output /src/chimaera-rmw
COPY gem5-transport/ /src/gem5-transport/
COPY ros/chimaera_ros_ws/src/ /src/ros/chimaera_ros_ws/src/
RUN source /opt/ros/humble/setup.bash \
    && CMAKE_BUILD_PARALLEL_LEVEL=${BUILD_JOBS} colcon --log-base /tmp/dds-log build \
       --base-paths /src/ros/chimaera_ros_ws/src /src/chimaera-rmw \
       --packages-select chimaera_dds_transport rmw_fastrtps_shared_cpp rmw_fastrtps_cpp chimaera_ros_bridge \
       --merge-install --build-base /tmp/dds-build --install-base /opt/chimaera/ros \
       --executor sequential --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
       -DBUILD_GAZEBO_BRIDGE=ON -DBUILD_LATENCY_BENCHMARK=ON -DGEM5_ROOT=/src/gem5 \
       -DGEM5_M5_LIBRARY=/opt/chimaera/gem5/util/m5/build/x86/out/libm5.a \
    && rm -rf /tmp/dds-build /tmp/dds-log
ENV CHIMAERA_SETUP=/opt/chimaera/ros/local_setup.bash
