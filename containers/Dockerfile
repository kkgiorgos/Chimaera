ARG BASE_IMAGE=ubuntu:22.04@sha256:281c5745f657873d78e5531fc5ba8575f46ab7769b94550ac99543f122679986
FROM ${BASE_IMAGE} AS dependencies
ARG STACK_PROFILE=jammy-humble-fortress
ARG ROS_DISTRO=humble
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 ROS_DISTRO=${ROS_DISTRO} PYTHONDONTWRITEBYTECODE=1
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY containers/profiles/${STACK_PROFILE}.json /opt/chimaera/profile.json
COPY containers/scripts/install-build-env.py /opt/chimaera/scripts/install-build-env.py
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl python3 \
    && python3 /opt/chimaera/scripts/install-build-env.py \
    && rm -rf /var/lib/apt/lists/*

FROM dependencies AS gem5-build
ARG BUILD_JOBS=3
COPY gem5/ /src/gem5/
COPY containers/scripts/compile-gem5.sh /opt/chimaera/scripts/compile-gem5.sh
RUN --mount=type=cache,id=chimaera-${STACK_PROFILE}-compile,target=/var/cache/chimaera,sharing=locked \
    /bin/bash /opt/chimaera/scripts/compile-gem5.sh "${BUILD_JOBS}"

FROM gem5-build AS bridge-build
COPY gem5-transport/ /src/gem5-transport/
COPY ros/chimaera_ros_ws/src/ /src/ros/chimaera_ros_ws/src/
RUN source "/opt/ros/${ROS_DISTRO}/setup.bash" \
    && colcon --log-base /tmp/bridge-log build --base-paths /src/ros/chimaera_ros_ws/src \
       --build-base /tmp/bridge-build --merge-install --install-base /opt/chimaera/ros \
       --executor sequential --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
       -DBUILD_GAZEBO_BRIDGE=ON -DGEM5_ROOT=/src/gem5 \
       -DGEM5_M5_LIBRARY=/opt/chimaera/gem5/util/m5/build/x86/out/libm5.a \
    && rm -rf /tmp/bridge-build /tmp/bridge-log
COPY gem5/LICENSE gem5/COPYING /opt/chimaera/licenses/gem5/
COPY gem5/ext/systemc/LICENSE gem5/ext/systemc/NOTICE /opt/chimaera/licenses/systemc/
COPY gem5/ext/pybind11/LICENSE /opt/chimaera/licenses/pybind11/
RUN python3 -c 'import json,pathlib; p=pathlib.Path("/opt/chimaera"); (p/"build-manifest.json").write_text(json.dumps({"profile":json.loads((p/"profile.json").read_text())})+"\n")'

# Keep shared compilers and libraries so applications can build against the
# identical environment and export an overlay via a bind mount.
FROM bridge-build AS image
COPY containers/scripts/entrypoint.sh /opt/chimaera/scripts/entrypoint.sh
ENV ROS_LOCALHOST_ONLY=1
WORKDIR /tmp
ENTRYPOINT ["/bin/bash", "/opt/chimaera/scripts/entrypoint.sh"]
CMD ["/bin/bash"]
