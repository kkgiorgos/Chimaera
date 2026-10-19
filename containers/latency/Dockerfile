ARG SDK_IMAGE=chimaera:jammy-humble-fortress
FROM ${SDK_IMAGE}
USER root
ARG BUILD_JOBS=2
COPY gem5-transport/ /repo/gem5-transport/
COPY ros/chimaera_ros_ws/src/ /repo/ros/chimaera_ros_ws/src/
# Keep the standard source-tree layout for the workbench's Build and stage action.
RUN ln -s /opt/chimaera/gem5 /repo/gem5 \
    && ln -s /src/gem5/include /opt/chimaera/gem5/include \
    && ln -s /src/gem5/util/m5/src /opt/chimaera/gem5/util/m5/src
RUN source /opt/ros/humble/setup.bash \
    && CMAKE_BUILD_PARALLEL_LEVEL=${BUILD_JOBS} colcon --log-base /tmp/latency-log build \
       --base-paths /repo/ros/chimaera_ros_ws/src --build-base /tmp/latency-build \
       --merge-install --install-base /opt/chimaera/latency-runtime --executor sequential \
       --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON \
       -DBUILD_LATENCY_BENCHMARK=ON -DBUILD_GAZEBO_BRIDGE=ON \
    && ctest --test-dir /tmp/latency-build/chimaera_ros_bridge --output-on-failure \
    && source /opt/chimaera/latency-runtime/local_setup.bash \
    && python3 -m chimaera_ros.latency stage --output /opt/chimaera/latency-guest \
       --bundle /opt/chimaera/latency-guest.tar.gz \
    && rm -rf /tmp/latency-build /tmp/latency-log
COPY containers/latency/start.py /opt/chimaera/scripts/start-latency.py
ENV CHIMAERA_SETUP=/opt/chimaera/latency-runtime/local_setup.bash
WORKDIR /repo
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/defaults', timeout=3).read()"
CMD ["python3", "/opt/chimaera/scripts/start-latency.py"]
