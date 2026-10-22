#!/bin/bash
# One fresh-container invocation of the frozen runner. No transport edits.
set -eo pipefail
tooling_path=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
checkout_path=$(cd -- "$tooling_path/../.." && pwd)
matrix_path=$1
suite_path=$2
case_name=$3
shift 3
while [ "$#" -gt 0 ]; do
  case "$1" in
    --lock) lock_path=$2 ;;
    --gate) gate_path=$2 ;;
    --middleware-setup) middleware_setup=$2 ;;
    --runtime-setup) runtime_setup=$2 ;;
    --completion-record) completion_record=$2 ;;
    --operator-token) operator_token=$2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift 2
done
source /opt/ros/humble/setup.bash
source "${middleware_setup:?}"
source "${runtime_setup:?}"
export LD_LIBRARY_PATH="$(dirname -- "$runtime_setup")/lib:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="$checkout_path/ros/chimaera_ros_ws/src/chimaera_ros_bridge:$checkout_path/profiling/chimaera_rewrite:${PYTHONPATH:-}"
export PYTHONDONTWRITEBYTECODE=1
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$checkout_path"
cd -- "$checkout_path"
append_flags=()
if [ -f "$suite_path/suite.json" ]; then append_flags=(--append); fi
python3 - "$tooling_path/run.py" "${completion_record:?}" "${operator_token:?}" "$case_name" \
  "$matrix_path" --lock "${lock_path:?}" --gate "${gate_path:?}" --output "$suite_path" \
  "${append_flags[@]}" --cases "$case_name" <<'PY'
import json
from pathlib import Path
import runpy
import sys

runner, marker, token, case_name = sys.argv[1:5]
sys.path.insert(0, str(Path(runner).parent))
sys.argv = [runner, *sys.argv[5:]]
try:
    runpy.run_path(runner, run_name='__main__')
except SystemExit as result:
    if result.code in (0, 1):
        Path(marker).write_text(json.dumps(dict(operator_token=token, case=case_name,
                                               runner_return_code=result.code)) + '\n')
    raise
PY
