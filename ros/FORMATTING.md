C++ formatting uses the ROS 2 [ament_clang_format configuration](https://github.com/ament/ament_lint/blob/rolling/ament_clang_format/ament_clang_format/configuration/.clang-format)
in the root `.clang-format`. It covers layout; naming and other semantic rules
in the ROS 2 style guide still require review.

Install `clang-format` and clangd using your system package manager. From this
repository root, format individual files or a package directory:

```sh
python3 scripts/format_cpp.py wall_follow_ws/src/wall_follow_benchmark
python3 scripts/format_cpp.py path/to/file.cpp path/to/header.hpp
python3 scripts/format_cpp.py --check wall_follow_ws/src/wall_follow_benchmark
```

`--check` returns a nonzero status when formatting is needed without changing
files. Directory scans skip hidden directories and build, install, and log
outputs. Set `CLANG_FORMAT=clang-format-15` to select a particular executable;
use the same version across contributors for consistent results.

The closest `.clang-format` takes precedence, including the existing
`franka_ws/src/.clang-format` (Chromium style). The script and clangd both respect
this override.

Enable clangd in your editor and use its **Format Document** action (or your
editor's format-on-save setting). Clangd reads `.clangd` for language-server
settings and `.clang-format` for formatting.

For ROS includes and compiler flags, source your ROS environment and build from
the relevant workspace with:

```sh
colcon build --cmake-args -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
```

Clangd searches ancestor directories and their `build/` directories for
`compile_commands.json`. If your colcon setup only emits per-package databases,
merge them into the workspace root after building (run from that workspace):

```sh
python3 - <<'PY'
import json
from pathlib import Path

databases = sorted(Path('build').glob('*/compile_commands.json'))
if not databases:
    raise SystemExit('No compilation databases found; build C++ packages first')
commands = [entry for path in databases for entry in json.loads(path.read_text())]
Path('compile_commands.json').write_text(json.dumps(commands, indent=2) + '\n')
PY
```

Regenerate the database when build flags or source files change. Compilation
databases are only needed for code analysis; formatting works without a build.
