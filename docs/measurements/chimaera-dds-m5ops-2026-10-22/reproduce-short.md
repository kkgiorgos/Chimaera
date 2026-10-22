# Reproduce the short comparison

The short stage is the approved replacement for the cancelled long matrix. It
contains ten TimingSimpleCPU cases and two shared native-free SHM references.
There are no stepped native cases. Each source window lasts 0.2 seconds, with
0.05-second warmup and 0.1-second drain.

The local immutable implementation assets live under `.chimaera-m5/matched/`.
The selection is `.chimaera-m5/selections.r2.json`; the tooling snapshot for this
run is `.chimaera-m5/tooling-source-r3.tar.gz`. The historical assets, guest disk,
kernel and built simulator are external artifacts, not reconstructed merely by
checking out source. The READY binary is identified independently; exact
correspondence to its retained source snapshot remains unverified.

Generate the fixed matrix without starting a runtime:

```sh
python3 profiling/chimaera_m5/matrix.py --stage short --output NEW_MATRIX.json
```

Use the pinned SDK image
`sha256:c841ebb1000488c0010e21bc32a62b9d432b1a47e47f64e16501dc53bd66c8d4`.
Inside it, mount the checkout at `/work`, source ROS Humble, the pinned
middleware prefix and the candidate runtime, then run the tooling checks and
freeze a **new** lock. The original preparation script is retained locally at
`.chimaera-m5/prepare-short-r3.sh`; it verifies that old assets and implementation
identities remain unchanged. An independent new freeze has a different creation
timestamp and lock hash.

Run from the host using a fresh output directory:

```sh
python3 profiling/chimaera_m5/run_isolated.py NEW_MATRIX.json \
  --lock NEW_LOCK.json \
  --gate .chimaera-m5/first-catch-pass.json \
  --output NEW_SUITE \
  --operator-timeout 2100
```

The wrapper takes the immutable SDK image from the lock and launches one fresh
container per case, serially, with four CPUs of quota, 24 GiB RAM, 2 GiB SHM,
KVM access, disabled external networking and private `/tmp`. It retains failed
attempts and refuses to overwrite them on resume. Cleanup is restricted to its
own labelled container. The gate attests the successful actual KVM ball-catching
experiment and checks the retained evidence hashes.

The executed parent sequence is `.chimaera-m5/timing-pipeline-r3.py`. It runs the
shared ball reference and new backend first, validates the detailed CPU and
reference evidence, then runs the remaining approved cases and aggregates them.
Its log and result are `.chimaera-m5/timing-pipeline-r3.log` and
`.chimaera-m5/timing-pipeline-r3-result.json`.

Aggregate inside the SDK with the same sourced environment and source package
first in `PYTHONPATH`:

```sh
python3 profiling/chimaera_m5/aggregate.py NEW_SUITE --output NEW_REPORT
```

The short matrix declares fixed receipt-time windows and two explicit shared
free-native references. Configuration, DDS XML and common endpoint/clock/RMW
hashes must match; no stepped reference is required. Preserve source counts,
missing samples, age and release lag alongside latency. These windows describe
a transient overlap experiment, not maximum capacity or confidence-based tail
non-regression. The new payload path uses functional/coherent copies instead
of timed DMA payload requests, so its memory-cost model must be stated whenever
results are compared.
