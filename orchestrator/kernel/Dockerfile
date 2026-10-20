FROM ubuntu:22.04@sha256:281c5745f657873d78e5531fc5ba8575f46ab7769b94550ac99543f122679986
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential bc bison flex libelf-dev libssl-dev kmod python3 ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*
ENV LANG=C LC_ALL=C SOURCE_DATE_EPOCH=1704067200 KBUILD_BUILD_TIMESTAMP="Mon Jan 1 00:00:00 UTC 2024" \
    KBUILD_BUILD_USER=chimaera KBUILD_BUILD_HOST=builder
ENTRYPOINT ["python3", "/recipe/build.py"]
