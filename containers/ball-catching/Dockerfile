ARG SDK_IMAGE=chimaera:jammy-humble-fortress
FROM ${SDK_IMAGE}
ARG BUILD_JOBS=2
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libeigen3-dev liborocos-kdl-dev libopencv-dev ros-humble-urdf \
      ros-humble-trajectory-msgs ros-humble-std-srvs ros-humble-robot-state-publisher \
      ros-humble-xacro python3-numpy python3-pytest \
    && rm -rf /var/lib/apt/lists/*
COPY ros/franka_ws/src/franka_description/ /src/ball/franka_description/
COPY ros/ball_catching_ws/src/ /src/ball/packages/
RUN source /opt/ros/humble/setup.bash && source /opt/chimaera/ros/local_setup.bash \
    && CMAKE_BUILD_PARALLEL_LEVEL=${BUILD_JOBS} colcon --log-base /tmp/ball-log build \
       --base-paths /src/ball --build-base /tmp/ball-build --install-base /opt/chimaera/ball \
       --executor sequential --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
    && rm -rf /tmp/ball-build /tmp/ball-log
ENV CHIMAERA_SETUP=/opt/chimaera/ball/local_setup.bash LIBGL_ALWAYS_SOFTWARE=1
