# Message-latency workbench in Docker

The backend container serves the web UI and runs ROS 2 Humble, Gazebo Fortress,
gem5 and the latency endpoints. Host ROS/Gazebo installations are unnecessary.
The ball-catching traffic preset supports one gem5 boot per hardware profile.
The existing disk and kernel are mounted read-only; each gem5 session uses its
own copy-on-write disk layer. Results, logs and workbench history persist on the host.

From the repository root, first build the shared SDK if a compatible local SDK
image is not already available:

```bash
python3 containers/build.py --jobs 2
```

Copy and edit the local environment file:

```bash
cp containers/latency/.env.example containers/latency/.env
mkdir -p .chimaera-latency/docker
id -u
id -g
stat -c %g /dev/kvm
```

Set `CHIMAERA_DISK` and `CHIMAERA_KERNEL` to existing absolute file paths,
`CHIMAERA_DATA` to the result directory above, the UID/GID to your user IDs,
and `CHIMAERA_KVM_GID` to the device's numeric group. Set `CHIMAERA_BIND` to
your LAN IP for access from other devices, or `127.0.0.1` for local access.
`CHIMAERA_SDK_IMAGE` can select another compatible local SDK image, such as
`chimaera:orchestrator-sdk`. Linux x86-64, Docker Compose and working KVM are
required. The backend does not mount the host Docker socket.

Build and start:

```bash
docker compose --env-file containers/latency/.env \
  -f containers/latency/compose.yaml up -d --build
```

The image builds the current bridge and benchmark, runs CTest and stages a v4
guest bundle. Runtime paths are initialized automatically inside the container;
the workbench should show both native and Chimaera readiness. Open
`http://YOUR_LAN_IP:8787`, choose **Ball-catching traffic**, and run a sweep.
Hardware counts and clock/cache settings describe the simulated machine.

The guest launches its staged endpoint by absolute executable path. It does
not depend on the optional `ros2 run` CLI plugin, which is absent from some
existing Chimaera SDK/guest images.

The UI's **Build and stage** action can rebuild the included source snapshot
into `/data` without installing host packages. To include new repository edits,
rerun the Compose build/start command. A container restart retains the selected
runtime, results and history. Stop a running sweep before rebuilding/recreating
the backend. The service restarts automatically after Docker/host restarts.

```bash
docker compose --env-file containers/latency/.env \
  -f containers/latency/compose.yaml logs -f backend
docker compose --env-file containers/latency/.env \
  -f containers/latency/compose.yaml down
```

Bind the workbench only on a trusted network: it has no login and controls
the simulation processes inside its container. Only the HTTP port is published;
ROS discovery, Gazebo transport and the fixed Chimaera sockets stay inside the
container. The service has a read-only filesystem with writable `/data`, `/tmp`
and shared memory. No public port forwarding is needed for LAN access.

Validation on 2026-10-19 passed all 11 CTest targets and real native/KVM traffic
checks. Two KVM cases with 50/20 ms synchronization shared one guest boot and
both received all five measured stream types. The short checks used 64×64
images at 20 Hz and 100 Hz joints/control; they validate execution and session
reuse rather than full-preset performance. Results remain in workbench history.
