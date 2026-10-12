#!/usr/bin/env bash
# Download the pinned Ramulator 2.1 and build its C++ library.
set -euo pipefail

integration_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$integration_dir/ramulator2"
revision="$(cat "$integration_dir/REVISION")"
jobs="${1:-2}"
if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    echo "Usage: bash $0 [positive-build-job-count]" >&2
    exit 2
fi

if [[ ! -d "$source_dir" ]]; then
    git clone https://github.com/CMU-SAFARI/ramulator2.git "$source_dir"
    git -C "$source_dir" checkout --detach "$revision"
fi
if [[ "$(git -C "$source_dir" rev-parse HEAD)" != "$revision" ]]; then
    echo "Existing Ramulator checkout differs from REVISION; use a separate checkout." >&2
    exit 1
fi

# Generated DRAM sources are checked in; nanobind/codegen are unnecessary.
cmake -S "$source_dir" -B "$source_dir/build" \
    -DCMAKE_BUILD_TYPE=Release -DRAMULATOR_PYTHON_BINDINGS=OFF
cmake --build "$source_dir/build" --target ramulator --parallel "$jobs"
