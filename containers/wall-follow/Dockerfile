ARG BASE_IMAGE=ubuntu:22.04@sha256:281c5745f657873d78e5531fc5ba8575f46ab7769b94550ac99543f122679986
FROM ${BASE_IMAGE} AS build-env

ARG STACK_PROFILE=jammy-humble-fortress
ARG ROS_DISTRO=humble
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    ROS_DISTRO=${ROS_DISTRO} \
    PYTHONDONTWRITEBYTECODE=1
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY containers/wall-follow/profiles/${STACK_PROFILE}.json /opt/chimaera/profile.json
COPY containers/wall-follow/scripts/install-build-env.py /opt/chimaera/scripts/install-build-env.py
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl python3 \
    && python3 /opt/chimaera/scripts/install-build-env.py \
    && rm -rf /var/lib/apt/lists/*
COPY containers/wall-follow/scripts/builder-entrypoint.sh containers/wall-follow/scripts/verify-build-env.sh /opt/chimaera/scripts/

WORKDIR /src/ros/wall_follow_ws
ENTRYPOINT ["/bin/bash", "/opt/chimaera/scripts/builder-entrypoint.sh"]
CMD ["/bin/bash"]

FROM build-env AS gem5-build
ARG BUILD_JOBS=3
COPY gem5/ /src/gem5/
COPY containers/wall-follow/scripts/compile-gem5.sh /opt/chimaera/scripts/compile-gem5.sh
RUN --mount=type=cache,id=chimaera-${STACK_PROFILE}-compile,target=/var/cache/chimaera,sharing=locked \
    /bin/bash /opt/chimaera/scripts/compile-gem5.sh "${BUILD_JOBS}"

FROM gem5-build AS builder
ARG BUILD_JOBS=3
COPY gem5-transport/ /src/gem5-transport/
COPY queue-manager/ /src/queue-manager/
COPY ros/wall_follow_ws/src/ /src/ros/wall_follow_ws/src/
COPY ros/wall_follow_ws/benchmarking/ /src/ros/wall_follow_ws/benchmarking/
COPY containers/wall-follow/scripts/compile-ros.sh /opt/chimaera/scripts/compile-ros.sh
RUN /bin/bash /opt/chimaera/scripts/compile-ros.sh "${BUILD_JOBS}"
COPY containers/wall-follow/scripts/manifest.py containers/wall-follow/scripts/verify-artifacts.py containers/wall-follow/scripts/verify-builder.sh containers/wall-follow/scripts/verify-gem5.py containers/wall-follow/scripts/verify-gazebo.py /opt/chimaera/scripts/
COPY gem5/LICENSE gem5/COPYING /opt/chimaera/licenses/gem5/
COPY gem5/ext/systemc/LICENSE gem5/ext/systemc/NOTICE /opt/chimaera/licenses/systemc/
COPY gem5/ext/pybind11/LICENSE /opt/chimaera/licenses/pybind11/
ARG SOURCE_REVISION
ARG SOURCE_TREE_SHA256
ARG SOURCE_DIRTY
RUN python3 /opt/chimaera/scripts/manifest.py "${SOURCE_REVISION}" "${SOURCE_TREE_SHA256}" "${SOURCE_DIRTY}" \
    && /bin/bash /opt/chimaera/scripts/builder-entrypoint.sh \
        /bin/bash /opt/chimaera/scripts/verify-builder.sh

FROM scratch AS artifacts
COPY --from=builder /opt/chimaera/gem5/ /gem5/
COPY --from=builder /opt/chimaera/ros/ /ros/
COPY --from=builder /opt/chimaera/build-manifest.json /build-manifest.json
COPY --from=builder /opt/chimaera/packages.tsv /packages.tsv
COPY --from=builder /opt/chimaera/profile.json /profile.json
COPY --from=builder /opt/chimaera/licenses/ /licenses/

FROM builder AS runtime-payload
COPY containers/wall-follow/profiles/${STACK_PROFILE}.runtime.json /opt/chimaera/runtime-profile.json
COPY containers/wall-follow/scripts/stage-runtime.py /opt/chimaera/scripts/stage-runtime.py
RUN python3 /opt/chimaera/scripts/stage-runtime.py

FROM ${BASE_IMAGE} AS worker
ARG ROS_DISTRO=humble
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    ROS_DISTRO=${ROS_DISTRO} \
    ROS_DOMAIN_ID=41 \
    ROS_LOCALHOST_ONLY=1 \
    PYTHONDONTWRITEBYTECODE=1
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY --from=runtime-payload /runtime/etc/apt/ /etc/apt/
COPY --from=runtime-payload /runtime/etc/ssl/certs/ /etc/ssl/certs/
COPY --from=runtime-payload /runtime/usr/share/keyrings/ /usr/share/keyrings/
COPY --from=runtime-payload /runtime/usr/share/ca-certificates/ /usr/share/ca-certificates/
COPY --from=runtime-payload /runtime/opt/chimaera/profile.json /runtime/opt/chimaera/runtime-profile.json /runtime/opt/chimaera/packages.tsv /opt/chimaera/
COPY containers/wall-follow/scripts/install-runtime.py /opt/chimaera/scripts/install-runtime.py
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 \
    && python3 /opt/chimaera/scripts/install-runtime.py \
    && rm -rf /var/lib/apt/lists/* /opt/chimaera/scripts/install-runtime.py
COPY --from=runtime-payload /runtime/ /
COPY ros/wall_follow_ws/scripts/run_experiments.py /opt/chimaera/ws/scripts/run_experiments.py
COPY ros/wall_follow_ws/benchmarking/ /opt/chimaera/ws/benchmarking/
COPY ros/wall_follow_ws/experiments/ /opt/chimaera/ws/experiments/
COPY ros/wall_follow_ws/src/wall_follow_robot/src/ /opt/chimaera/ws/src/wall_follow_robot/src/
COPY ros/wall_follow_ws/src/wall_follow_robot/include/ /opt/chimaera/ws/src/wall_follow_robot/include/
COPY containers/wall-follow/scripts/worker-entrypoint.sh containers/wall-follow/scripts/worker-manifest.py containers/wall-follow/scripts/verify-worker.py containers/wall-follow/scripts/verify-gem5.py containers/wall-follow/scripts/verify-gazebo.py /opt/chimaera/scripts/
RUN python3 /opt/chimaera/scripts/worker-manifest.py \
    && /bin/bash /opt/chimaera/scripts/worker-entrypoint.sh python3 /opt/chimaera/scripts/verify-worker.py \
    && rm -rf /tmp/chimaera-home /tmp/worker-check
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["/bin/bash", "/opt/chimaera/scripts/worker-entrypoint.sh"]
CMD ["python3", "/opt/chimaera/scripts/verify-worker.py"]
