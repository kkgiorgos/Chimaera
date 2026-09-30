# Wall follow Docker workers

The `jammy-humble-fortress` profile pins an Ubuntu 22.04 amd64 base image and
repository bootstrap downloads. The builder compiles custom x86 gem5, libm5,
and the three wall follow ROS packages. Run these commands from the repository root.

The profile centralizes base-image and package choices. The current bridge targets
Ignition Transport 11 and Messages 8, and guest startup sources Humble. Changing
ROS or Gazebo generations also requires checking those source and launch APIs.

Build and check the dependency environment:

```sh
python3 containers/wall-follow/build.py --target build-env
docker run --rm --network none chimaera-build-env:jammy-humble-fortress \
    bash /opt/chimaera/scripts/verify-build-env.sh
```

Build and check the executables:

```sh
python3 containers/wall-follow/build.py --target builder --jobs 3
docker run --rm --network none chimaera-builder:jammy-humble-fortress \
    bash /opt/chimaera/scripts/verify-builder.sh
```

Check the generated world's headless lidar and ROS adapter with an Intel render
device available to the container:

```sh
docker run --rm --network none --device /dev/dri/renderD128 \
    chimaera-builder:jammy-humble-fortress \
    python3 /opt/chimaera/scripts/verify-gazebo.py
```

Choose the render device for the machine running the worker. This check requires
two scans, a 720-sample scan with at least 600 valid ranges, and an advancing
simulation clock.

Export the compiled files and manifests:

```sh
python3 containers/wall-follow/build.py --target artifacts
python3 containers/wall-follow/scripts/verify-artifacts.py --export containers/wall-follow/artifacts
```

The export defaults to `containers/wall-follow/artifacts` and requires an empty
destination. Use `--output` to select a new directory for a later export. `packages.tsv`
records resolved Debian package versions. `build-manifest.json` records the
source revision, working tree status, a build input digest, controller source
hashes, checksums and permission modes for the staged gem5, ROS, and license files. Repository
packages are resolved during each uncached build; the base digest alone does
not freeze those package versions. Preserve the built image and manifests to
identify the exact environment used by an experiment.

For an interactive builder shell:

```sh
docker run --rm -it chimaera-builder:jammy-humble-fortress
```

The builder is an intermediate artifact. Its checks import gem5's required CPU
types, check the cache protocol build flag, and verify executable linkage.
They do not boot a guest.

gem5 and libm5 objects stay in a locked BuildKit cache, separated by the resolved
SDK package digest. Interrupted builds can reuse those objects. The builder image
contains the staged executables and libm5 archive; the cache contents are not
part of the image.

## Build the runtime

```sh
python3 containers/wall-follow/build.py --target worker --jobs 5
```

The worker starts from the pinned Ubuntu base. It contains stripped gem5 and ROS
executables, the experiment driver, and packaged system runtime libraries. It
contains no compilers, headers for building ROS, static archives, libm5 SDK, or
gem5 source/build cache. Four small controller source files remain so the
experiment driver can record the source hashes of the implementation it runs.
Plotting is disabled; analyze exported results outside the worker.

`profiles/jammy-humble-fortress.runtime.json` defines system runtime packages and
Fortress media/plugin aliases that upstream places in development packages.
The ROS prefixes come from the builder, avoiding ROS bridge package dependencies
on development packages. Copied package licenses are retained.

System packages, including dependencies, must match the SDK's recorded versions.
A rebuild fails if those versions are no longer available. Keep the image for
repeatable experiments. `build-manifest.json` records original SDK artifacts;
`worker-manifest.json` records transformed runtime files, modes, symlinks, copied
licenses, scripts, and `runtime-packages.tsv`. The worker's numeric default user
is 1000:1000; the launcher uses the invoking host user's UID and GID.

## Check the worker

```sh
python3 containers/wall-follow/worker.py check --output results/worker-check
```

This checks payload integrity, the inventoried SDK ELF libraries, gem5 features, KVM
API/VM creation, fixed socket paths, and actual headless lidar/clock delivery.
It requires access to `/dev/kvm` and the selected render device. Use
`--render-device /dev/dri/renderD128` to select another machine's render node,
or `--software-rendering` for Mesa software rendering. `check --skip-kvm` allows
a rendering-only check on a machine without KVM. A full gem5 run requires KVM.

Run the concurrent worker and cleanup verification:

```sh
python3 containers/wall-follow/verify-workers.py --output results/worker-verification
```

It checks two simultaneous workers with the same socket names, ROS domain, and
Gazebo partition; two simultaneous short C++ controller suites; resume behavior;
read-only input mounts; refusal of a second output writer; SIGINT cleanup; and
cancellation while a delayed Docker client has not created a container.
It saves Docker inspection snapshots, logs, recorded poses/commands, and a result
summary. Use a new verification directory for each invocation.

## Run a suite

First verify the native controller, without a guest:

```sh
python3 containers/wall-follow/worker.py run --local \
    --config containers/wall-follow/experiments/smoke.json \
    --output results/local-check --architecture local --warmup 0
```

### Prepare the guest

Build the guest from the local builder image:

```sh
python3 containers/wall-follow/prepare-guest.py
```

This creates `containers/wall-follow/guest-assets/jammy-humble-fortress/` with
`disk.img`, `kernel`, `manifest.json`, payload/package inventories, and filesystem
inspection results. The directory is excluded from git and Docker build context.
The command uses the cached gem5 kernel when it matches the pinned checksum,
or downloads and verifies that resource. Use `--kernel PATH` for an explicit
local kernel and `--sdk-image IMAGE` to choose a local SDK.

The guest contains Ubuntu 22.04 userspace, the SDK's ROS C++ dependency closure,
the controller, guest bridge, configuration, and address-mode m5 utility.
Gazebo and gem5 run in the worker. Headers, build metadata, and static archives
are removed from the ROS payload. System libraries use exact SDK package versions;
package ownership, licenses, source manifests, file hashes, and modes are retained.
No host mounts, loop devices, or host package installation are needed.

`profiles/jammy-humble-fortress.guest.json` defines the retained ROS package roots,
disk size, disk identifier, filesystem UUID, and pinned kernel. A small root init
mounts the guest filesystems, enables loopback, and executes the injected script
through address-mode `m5 readfile`. The root is ext4 on MBR partition 2; its
PARTUUID reaches gem5 through the launch arguments.

Output publication is atomic. A repeated invocation verifies and reuses matching
assets. A changed SDK or construction recipe requires a new `--output` directory;
published disks are never edited. The disk is sparse: its logical capacity is
about 1 GiB, while its allocated storage is much smaller. Preserve holes when
copying it, for example with `cp --sparse=always` or `tar --sparse`.

### Run with gem5

```sh
python3 containers/wall-follow/worker.py run \
    --config ros/wall_follow_ws/experiments/demo.json \
    --output results/worker-01 --architecture baseline \
    --guest-assets containers/wall-follow/guest-assets/jammy-humble-fortress \
    --cpus 2 --memory 8g
```

`--guest-assets` verifies disk and kernel content checksums, requires a completed
manifest, checks the worker's stack profile, and selects the manifest's root
PARTUUID. Each invocation performs these checks before creating a container.
For an externally prepared disk, `--guest-image PATH --kernel PATH` remains
available and uses `/dev/sda2`. The official Ubuntu gem5 image has root partition
1 and cannot be passed directly through that interface.

The guest was checked with direct QEMU root boot and short TimingSimpleCPU
gem5/Gazebo runs, including two concurrent workers sharing the disk. These runs
recorded commands, robot movement, and gem5 CPU statistics; the base disk checksum
stayed unchanged. Long sweeps and O3 workloads need their own runtime checks.

The launcher resolves the Docker image to its immutable local ID and records it,
the command, configuration checksum, and guest file identity in `worker.json`.
The launcher requires a nonroot host user. Each invocation keeps a separate
`launches/` log and record. Results live in
`suite/`. Add `--resume` to retry an interrupted matching suite or skip completed
runs. Changing the image, configuration, CPU/memory limits, rendering device or
mode, warmup, or guest file identity rejects resume. Guest file identity records
path, inode, size, and modification time. `--guest-assets` also records the manifest
checksum and checks its disk/kernel hashes. Keep inputs immutable while workers use them.

Each worker has private network, IPC, `/tmp`, `/dev/shm`, home, cache, and ROS
logs. Only its output directory is mounted writable. Configuration, kernel, and
guest disk are mounted read-only. gem5 uses an in-memory `CowDiskImage` over the
shared read-only disk, so parallel workers do not copy the disk. The launcher
grants KVM/render device access and the numeric groups needed for devices and
input/output files. Its default
limits are 2 CPUs, 8 GiB memory with no additional swap, 512 processes, 256 MiB
shared memory, and 512 MiB temporary files.

Start another foreground command with a different output directory to run in
parallel. Split the DSE configuration between workers; the launcher does not
partition a sweep. Configurations must set `gui=false`. The launcher expects a
local Linux amd64 Docker daemon, and never mounts host home, `/tmp`, or Docker's
socket. Docker's [run options](https://docs.docker.com/engine/containers/run/)
and [tmpfs mounts](https://docs.docker.com/engine/storage/tmpfs/) describe these
isolation and resource controls.

Later stacks need a new base/build profile, runtime profile, and guest profile, plus any source
changes needed for their ROS/Gazebo APIs. The current source targets Humble,
Ignition Transport 11, and Ignition Msgs 8.
