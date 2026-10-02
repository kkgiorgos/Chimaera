# Parallel benchmarks with Docker

Use one prepared image for many independent jobs. Each job gets a fresh
container, private network/IPC and `/tmp`, read-only inputs, and its own writable
`/output`. `--workers` limits concurrency; jobs enter the next free worker as
others finish. This lets independent gem5 simulations use multiple host cores.
The runner is Python standard library only and has no benchmark dependencies.

## Prepare an image once

You need Linux, Python 3.10+, access to a **local** Docker daemon, and enough RAM
for all concurrent simulations. Build or select an image containing your custom
gem5 binary, configuration scripts, Python dependencies, and any benchmark
software. Build from this repository's modified gem5 for Chimaera operations.
The image must support running as your host UID/GID with a read-only filesystem,
a writable `/tmp`, and output in `/output`. Its entrypoint should set up the
required environment and execute its arguments (`exec "$@"`). Put downloadable
resources in the image or supply them as inputs: workers have no network access.

The supplied universal image includes Ubuntu 22.04, ROS 2 Humble, Gazebo
Fortress, custom gem5/libm5, shared build tools, and the universal Chimaera bridge.
It contains no application workspace, worlds, benchmark scripts or experiments.
Build it from the repository root:

```bash
python3 containers/build.py --jobs 3
```

The stable tag is `chimaera:jammy-humble-fortress`. Dependency packages and pinned
repositories are in `containers/profiles/jammy-humble-fortress.json`. Edit the
profile or derive another image to satisfy additional system dependencies.
`--tag`, `--profile`, `--no-cache`, and `--print-command` customize the build.

Build benchmark application packages separately using this image, exporting
the compiled files to a bind-mounted host directory. Mount the source/scripts,
compiled application overlay, worlds, configurations and guest assets as inputs.
The image entrypoint sources ROS and the universal bridge. Set `CHIMAERA_SETUP`
to colon-separated setup files from mounted overlays, such as
`/assets/overlay/local_setup.bash`, to enable your application packages.
Keep compiled overlays compatible with the shared image's libraries.
The [wall-follow example](wall-follow/README.md) provides these setup steps.

You can also supply your own image. For example:

```dockerfile
FROM your-gem5-dependency-image
COPY gem5/build/X86/gem5.opt /opt/gem5/gem5.opt
WORKDIR /tmp
ENTRYPOINT []
```

Build the binary against compatible libraries, or compile it inside the image.
Supply a disk and kernel with the application's required guest dependencies for
full-system simulations. Use gem5's copy-on-write disk layer over a read-only
base disk; do not share writable disks between workers. The generic runner
requires no image labels, manifests, guest layout, or benchmark name. Validate
a new setup with one short job before running a large load.

## Configure and run

```json
{
  "image": "my-gem5:latest",
  "command": ["/opt/gem5/gem5.opt", "--outdir=/output", "/opt/benchmark/sim.py"],
  "mounts": [
    {"source": "guest/disk.img", "target": "/assets/disk.img"},
    {"source": "guest/kernel", "target": "/assets/kernel"}
  ],
  "devices": ["/dev/kvm"],
  "cpus": 1,
  "memory": "8g",
  "jobs": [
    {"name": "small", "args": ["--size", "small"]},
    {"name": "large", "args": ["--size", "large"]}
  ]
}
```

Adapt the arguments to your gem5 configuration, including its disk/kernel
options. Mount sources are relative to the **configuration file**, or absolute.
All configured inputs are read-only; `/output` is reserved for the individual job.
Inputs and the result directory must be separate trees. Commands are arrays,
passed directly to the image's entrypoint. Each job's `args` are appended to
`command`; use `["bash", "-lc", "..."]` explicitly if you need shell syntax.
For shell commands, pass job differences as environment variables.

Optional `env` maps variable names to string values, globally or per job (job
values override global values). `CHIMAERA_JOB` contains the job name. Optional
`workdir` selects an absolute container working directory. `devices` defaults to
none: add KVM only if your gem5 configuration uses it, and a render node only
if you use hardware graphics. Device groups are passed into workers. For a
software renderer, set `env` to `{"LIBGL_ALWAYS_SOFTWARE": "1"}`. For Gazebo,
set `IGN_PARTITION` to an explicit common value such as `chimaera` so its processes
share a discovery partition. Container network isolation separates workers. CPU and memory
limits default to 2 CPUs and 8 GiB per container. The image entrypoint remains
active, allowing it to source ROS or other runtime setup.

```bash
python3 containers/run.py --config my-run.json --output results/my-run --dry-run
python3 containers/run.py --config my-run.json --output results/my-run --workers 8
```

Choose concurrency to fit CPU **and** memory capacity. Progress prints every ten
seconds: completed/failed/queued totals plus elapsed time and the latest log line
for each active worker. Change this with `--progress-interval 5`. This shows
activity, not a simulation percentage (only the benchmark knows that).
`jobs/<name>/worker.log` holds console output; other files under that directory
are whatever the benchmark writes to `/output`. `run.json` records the resolved
image ID, configuration, Docker commands, exit codes, and job statuses. A failed
job does not stop other jobs; the runner returns nonzero if any job fails.

Ctrl+C stops active containers and records interruption. Repeat the command with
`--resume` to skip successful jobs and retry the others. The resolved image and
configuration must match; concurrency can change. Keep mounted inputs unchanged:
the runner deliberately does not hash large disks or directory trees. Retried
jobs retain their files and append their logs, so commands must handle existing
output (for example, a benchmark's own `--resume` option). After a host crash or
forced kill, remove any container named in the error before resume.

For a short container-only check with the shared image:

```bash
python3 containers/run.py --config containers/examples/commands.json \
  --output /tmp/chimaera-command-check --workers 2 --progress-interval 1
```

## Keep disk usage under control

Use stable tags instead of a new permanent image tag per build. Workers are
removed when they finish. Guest preparation removes its temporary SDK alias and
construction images; the runtime and build tools share the same universal image.
Old guest directories are ordinary inputs; remove ones you no longer need after
their runs finish.

Preview obsolete Chimaera tags and dangling construction images, then remove them:

```bash
python3 containers/clean-images.py
python3 containers/clean-images.py --apply
```

Defaults keep only `chimaera:jammy-humble-fortress`. For
custom images, pass every tag you need with repeated `--keep TAG`. This removes
only `chimaera` and `chimaera-*` tags and dangling images identified by Chimaera
labels or build history, without forcing deletion of images used by containers.
Keep older images if you intend to resume runs pinned to them. Tags sharing an
image do not duplicate its layers; removing stale tags allows old layers to be
freed when no references remain. Untagged images and build cache can also consume
space. Inspect `docker system df`; use `docker image prune` for dangling images,
and `docker builder prune` when you no longer need compilation cache. Those
Docker commands affect the daemon as a whole, so they are separate from our
scoped cleanup script.

Focused host tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q containers/tests containers/wall-follow/tests
```
