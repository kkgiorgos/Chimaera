# Container images and parallel benchmarks

Run independent jobs from one shared image. Each worker runs as your host UID/GID
with private network/IPC, a read-only filesystem and inputs, writable `/tmp`, and
its own `/output`. The runner uses only Python's standard library.

Run all commands from the repository root.

## Choose a workflow

| Workflow | Guide |
| --- | --- |
| Develop wall-follow with Gazebo | [Local application development](wall-follow/README.md#local-application-development) |
| Prepare, run, and report a wall-follow suite | [Automated benchmark suites](wall-follow/README.md#automated-benchmark-suites) |
| Run jobs for any benchmark | [Configure jobs](#configure-jobs) below |

## Requirements

Linux, Python 3.10+, a **local** Docker daemon, and sufficient CPU, RAM, and disk
space. Building the supplied Linux x86-64 image needs Docker Buildx and network
access; host ROS/Gazebo installations are unnecessary. Workers have no network
access, so download resources during setup or mount them as inputs.

KVM simulations additionally need read/write `/dev/kvm` access and hardware
virtualization (nested virtualization on a VM).

## Structure

| Component | Responsibility |
| --- | --- |
| [`build.py`](build.py), [`Dockerfile`](Dockerfile) | Build the shared image |
| [`profiles/`](profiles/), [`scripts/`](scripts/) | Stack dependencies, pinned repositories, compilation, and runtime setup |
| [`run.py`](run.py) | Validate, schedule, record, and resume isolated jobs |
| [`clean-images.py`](clean-images.py) | Remove obsolete Chimaera images |
| [`wall-follow/`](wall-follow/README.md) | Application setup, development, suites, and reports |
| [`tests/`](tests/) | Generic runner checks |

## Build the shared image

The image includes Ubuntu 22.04, ROS 2 Humble, Gazebo Fortress, custom gem5/libm5,
build tools, and the universal Chimaera bridge. Application workspaces, worlds,
experiments, and benchmark scripts are mounted separately.

```bash
python3 containers/build.py --jobs 3
```

The default tag is `chimaera:jammy-humble-fortress`. Dependencies are defined in
[`profiles/jammy-humble-fortress.json`](profiles/jammy-humble-fortress.json).

| Option | Purpose |
| --- | --- |
| `--jobs N` | Compilation parallelism; default 3 |
| `--profile NAME` | Stack profile; default `jammy-humble-fortress` |
| `--tag TAG` | Override `chimaera:<profile>` |
| `--no-cache` | Rebuild Docker layers without their cache |
| `--print-command` | Print the build command without running it |

Check the image and runner with three short jobs:

```bash
python3 containers/run.py --config containers/examples/commands.json \
  --output /tmp/chimaera-command-check --workers 2 --progress-interval 1
```

Each writes `jobs/<name>/result.txt`. Use a fresh output directory for another check.

## Prepare application inputs

Build application packages in the shared image and export them to a host overlay
directory. Mount the overlay, sources/scripts, worlds, configurations, and guest
assets as inputs. The entrypoint sources ROS and the universal bridge; set
`CHIMAERA_SETUP` to colon-separated overlay setup files, such as
`/assets/overlay/local_setup.bash`. Keep overlays compatible with the image's
libraries. See the [wall-follow setup](wall-follow/README.md#individual-setup-steps).

For another image, include the benchmark's binaries and dependencies. Chimaera
requires this repository's modified gem5, built against compatible libraries.
Support the worker permissions and writable paths described above; an entrypoint
that sets up the environment should finish with `exec "$@"`.

Full-system simulations need a matching guest disk and kernel. Use gem5's
copy-on-write disk layer over a read-only base disk, rather than sharing writable
disks. The generic runner requires no image labels or guest manifests.

## Configure jobs

Save a configuration such as `my-run.json`, adapting the command and disk/kernel
arguments to your gem5 configuration:

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

| Field | Meaning / default |
| --- | --- |
| `image` | Required local tag or image ID; resolved once for all workers |
| `command` | Required nonempty argument array; job `args` are appended |
| `jobs` | Required nonempty array; each job has a unique `name`, optional `args` and `env` |
| `mounts` | Read-only `source`/`target` pairs; default empty |
| `env` | String environment values; job values override global values |
| `devices` | Absolute host device paths; default empty |
| `cpus` | Per-worker CPU limit; default 2 |
| `memory` | Per-worker memory limit; default `8g`, with no additional swap |
| `workdir` | Absolute container directory; defaults to the image's working directory |

Mount sources are absolute or relative to the **configuration file**. Targets
must be absolute and must not overlap one another or reserved `/output`. Inputs
and results must be separate directory trees. Job names start with a letter or
digit and contain only letters, digits, `_`, `.`, or `-`; `CHIMAERA_JOB` holds the name.

Commands go directly to the image entrypoint. For shell syntax, use
`["bash", "-lc", "..."]` and pass job differences through environment variables.
Add devices only as needed; their groups are passed into workers. For software
rendering, set `LIBGL_ALWAYS_SOFTWARE=1`. For Gazebo, set `IGN_PARTITION=chimaera`
so processes within each isolated worker share a discovery partition.

## Run jobs

```bash
python3 containers/run.py --config my-run.json --output results/my-run --dry-run
python3 containers/run.py --config my-run.json --output results/my-run --workers 8
```

Validate a new setup with one short job, then choose concurrency to fit CPU and
RAM. Queued jobs start as workers become free. Progress shows job totals, elapsed
time, and each worker's latest log line. A failed job does not stop others; the
runner returns nonzero if any job fails.

| Option | Purpose |
| --- | --- |
| `--config PATH` | Required runner configuration |
| `--output PATH` | Required fresh/empty result directory, unless resuming |
| `--workers N` | Maximum concurrent jobs; default 1 |
| `--progress-interval SECONDS` | Progress interval; default 10 |
| `--dry-run` | Validate fields and mounted inputs; print resolved JSON without contacting Docker |
| `--resume` | Skip completed jobs and retry the others |

Execution requires the image locally and accessible devices; workers never pull
images. `--dry-run` does not check either.

## Read the results

| Path under the output directory | Contents |
| --- | --- |
| `run.json` | Resolved image/configuration, Docker commands, job statuses, and exit codes |
| `jobs/<name>/worker.log` | Container stdout and stderr |
| `jobs/<name>/` | The worker's `/output`; additional files depend on the benchmark |

See [wall-follow results](wall-follow/README.md#read-the-results) for dashboards.

## Resume a run

Ctrl+C stops active containers and records interruption. Repeat the command with
`--resume`. The resolved image and configuration must match; concurrency may change.
Keep mounted inputs unchanged: the runner does not hash disks or directory trees.
Retries retain files and append logs, so the benchmark must handle existing output.
After a crash or forced kill, stop/remove any old container named in the resume error.

## Keep disk usage under control

Use stable tags. Workers and temporary guest construction images are removed
automatically. Remove old guest directories after their runs finish.

Preview obsolete Chimaera tags and dangling construction images, then remove them:

```bash
python3 containers/clean-images.py
python3 containers/clean-images.py --apply
```

The default keeps `chimaera:jammy-humble-fortress`; repeated `--keep TAG` options
replace that default. Cleanup covers only `chimaera`/`chimaera-*` tags and dangling
images identified by Chimaera labels or build history, without forcing deletion
of images used by containers. Keep images needed for resume.

Inspect total usage with `docker system df`. `docker image prune` removes dangling
images; `docker builder prune` removes build cache. These affect the whole daemon,
unlike the scoped cleanup script. Tags sharing an image share its layers.

## Checks

Host tests require no image build or running benchmark:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q containers/tests containers/wall-follow/tests
```
