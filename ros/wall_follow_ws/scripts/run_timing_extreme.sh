#!/usr/bin/env bash
# Run the four-case PoC sequentially, then build one combined comparison.
set -eo pipefail

workspace=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
output="$workspace/results/timing-extreme-poc"
gem5_root="$workspace/../../gem5"
resume=false
dry_run=false
plot_only=false

usage() {
    cat <<'EOF'
Usage: run_timing_extreme.sh [OPTIONS]

Runs the large-cache cases (3GHz, 100MHz), then the tiny-cache cases
(3GHz, 100MHz), then creates combined plots and an offline dashboard.
Sources ROS Humble and this workspace automatically when running simulations.

  --output DIR     Results directory (default: workspace/results/timing-extreme-poc)
                   Suites go in DIR/large and DIR/tiny; plots in DIR/comparison.
  --gem5-root DIR  gem5 checkout (default: workspace/../../gem5)
  --resume         Skip completed cases and retry failed/missing cases.
  --plot-only      Rebuild the comparison from existing completed results.
  --dry-run        Validate and print both plans; start nothing and write nothing.
  -h, --help       Show this help.

Relative paths are resolved from the directory where you invoke this script.
Failures are retained, remaining cases continue, and completed cases are plotted.
An incomplete four-case run returns a nonzero status. Ctrl-C stops the sequence.
EOF
}

while (($#)); do
    case "$1" in
        --output|--gem5-root)
            if (($# < 2)) || [[ -z "$2" || "$2" == --* ]]; then
                printf 'Missing directory for %s\n' "$1" >&2
                exit 2
            fi
            if [[ "$1" == --output ]]; then output=$2; else gem5_root=$2; fi
            shift 2
            ;;
        --resume) resume=true; shift ;;
        --dry-run) dry_run=true; shift ;;
        --plot-only) plot_only=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

if $plot_only && { $resume || $dry_run; }; then
    printf '%s\n' '--plot-only cannot be combined with --resume or --dry-run.' >&2
    exit 2
fi
[[ "$output" == /* ]] || output="$PWD/$output"
[[ "$gem5_root" == /* ]] || gem5_root="$PWD/$gem5_root"
trap 'exit 130' INT
trap 'exit 143' TERM

if ! $dry_run && ! $plot_only; then
    if [[ -e "$output" ]] && ! $resume; then
        printf 'Output already exists: %s\nUse --resume or choose --output DIR.\n' "$output" >&2
        exit 2
    fi
    setups=("$workspace/install/setup.bash")
    if [[ "${ROS_DISTRO:-}" != humble ]]; then
        setups=(/opt/ros/humble/setup.bash "${setups[@]}")
    fi
    for setup in "${setups[@]}"; do
        if [[ ! -r "$setup" ]]; then
            printf 'Missing environment setup: %s\nBuild the ROS workspace first.\n' "$setup" >&2
            exit 2
        fi
        # ROS/colcon setup files may reference unset environment variables.
        source "$setup"
    done
    command -v ros2 >/dev/null || { printf 'ros2 is unavailable after sourcing the workspace.\n' >&2; exit 2; }
fi
set -u
export MPLBACKEND=Agg

overall_status=0
run_suite() {
    local profile=$1 status=0
    local -a flags=()
    $resume && flags+=(--resume)
    $dry_run && flags+=(--dry-run)
    printf '\nRunning %s-cache suite: %s/%s\n' "$profile" "$output" "$profile"
    if python3 "$workspace/scripts/run_gem5_experiments.py" \
        --config "$workspace/experiments/timing_extreme_${profile}.json" \
        --architecture timing-extreme-poc \
        --gem5-root "$gem5_root" --output "$output/$profile" \
        --interval-us 50000 --poll-us 10000 --ratio 1 \
        --startup-timeout 600 --warmup 0 --keep-going --progress --no-plot \
        "${flags[@]}"; then
        status=0
    else
        status=$?
    fi
    case "$status" in
        0) ;;
        1)
            printf 'The %s-cache suite has failed cases; continuing with retained results.\n' "$profile" >&2
            overall_status=1
            ;;
        *) printf 'The %s-cache suite stopped (exit %s).\n' "$profile" "$status" >&2; exit "$status" ;;
    esac
}

if ! $plot_only; then
    run_suite large
    run_suite tiny
fi
if $dry_run; then
    printf '\nDry run complete; no simulations or comparisons were started.\n'
    exit "$overall_status"
fi

printf '\nCreating combined plots and dashboard: %s/comparison\n' "$output"
if ! python3 "$workspace/scripts/compare_experiments.py" \
    "$output/large" "$output/tiny" --output "$output/comparison" --warmup 0; then
    printf 'Comparison failed; raw results remain in %s\n' "$output" >&2
    exit 1
fi

# Check coverage even in --plot-only mode: never report a partial comparison
# as a successful four-case exploration.
if ! python3 - "$workspace/scripts" "$output" <<'PY'
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from run_experiments import eligible

root = Path(sys.argv[2])
included = set(json.loads((root / 'comparison/included_runs.json').read_text()))
count = 0
for profile in ('large', 'tiny'):
    for case in ('case_001_rep_01', 'case_002_rep_01'):
        attempts = (root / profile / 'runs' / case).glob('attempt_*')
        complete = any(eligible(path) and str(path.resolve()) in included for path in attempts)
        count += int(complete)
        print(f'{profile}/{case}: {"completed and included" if complete else "MISSING OR FAILED"}')
print(f'Comparison covers {count}/4 completed cases.')
sys.exit(0 if count == 4 else 1)
PY
then
    overall_status=1
fi

printf '\nPlots: %s/comparison/\nDashboard: %s/comparison/dashboard.html\n' "$output" "$output"
if ((overall_status)); then
    printf 'Some cases failed or are missing. Use --resume to finish this exploration.\n' >&2
fi
exit "$overall_status"
