ARG SDK_IMAGE=chimaera:jammy-humble-fortress
ARG BASE_IMAGE=ubuntu:22.04@sha256:281c5745f657873d78e5531fc5ba8575f46ab7769b94550ac99543f122679986
FROM ${SDK_IMAGE} AS payload
USER 0:0
ARG STACK_PROFILE=jammy-humble-fortress
COPY --from=profiles ${STACK_PROFILE}.json /guest-profile.json
COPY profiles/${STACK_PROFILE}.guest.json /guest-policy.json
COPY --from=benchmark / /opt/chimaera/ros/
COPY scripts/stage-guest.py scripts/guest_session.py /
RUN --network=none /bin/bash /opt/chimaera/scripts/entrypoint.sh python3 /stage-guest.py

FROM ${BASE_IMAGE} AS rootfs
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY --from=payload /payload/etc/apt/ /etc/apt/
COPY --from=payload /payload/usr/share/keyrings/ /usr/share/keyrings/
COPY --from=payload /payload/opt/chimaera/ /opt/chimaera/
COPY scripts/install-runtime.py /install-runtime.py
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 \
    && python3 /install-runtime.py \
    && rm -rf /var/lib/apt/lists/* /install-runtime.py
COPY --from=payload /payload/opt/ros/ /opt/ros/
COPY --from=payload /payload/usr/ /usr/
COPY scripts/guest-init.sh /usr/sbin/init
RUN chmod 755 /usr/sbin/init
ENTRYPOINT []
CMD ["/bin/bash"]

FROM ${SDK_IMAGE} AS tools
USER 0:0
COPY scripts/build-guest-disk.py /build-guest-disk.py
ENTRYPOINT ["python3", "/build-guest-disk.py"]
