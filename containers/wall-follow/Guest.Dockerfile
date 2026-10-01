ARG SDK_IMAGE=chimaera-builder:jammy-humble-fortress
ARG BASE_IMAGE=ubuntu:22.04@sha256:281c5745f657873d78e5531fc5ba8575f46ab7769b94550ac99543f122679986
FROM ${SDK_IMAGE} AS payload
ARG STACK_PROFILE=jammy-humble-fortress
COPY profiles/${STACK_PROFILE}.json /guest-profile.json
COPY profiles/${STACK_PROFILE}.guest.json /guest-policy.json
COPY scripts/stage-guest.py scripts/guest_session.py /
RUN --network=none python3 /opt/chimaera/scripts/verify-artifacts.py /opt/chimaera \
    && python3 /stage-guest.py

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
RUN chmod 755 /usr/sbin/init \
    && LD_LIBRARY_PATH=/opt/ros/humble/lib python3 -c \
       'import pathlib,subprocess; files=list(pathlib.Path("/opt/ros").rglob("*"))+list(pathlib.Path("/usr/local/bin").glob("*"))+list(pathlib.Path("/opt/chimaera/wall_follow").rglob("*")); elves=[p for p in files if p.is_file() and not p.is_symlink() and p.open("rb").read(4)==b"\x7fELF"]; results=[subprocess.run(["ldd",str(p)],capture_output=True,text=True) for p in elves]; assert all("not found" not in r.stdout+r.stderr for r in results), "Unresolved guest ELF dependency"; print("Verified",len(elves),"guest ELF files")'
ENTRYPOINT []
CMD ["/bin/bash"]

FROM ${SDK_IMAGE} AS tools
COPY scripts/build-guest-disk.py /build-guest-disk.py
ENTRYPOINT ["python3", "/build-guest-disk.py"]
