# Repository Guidelines

## Project Structure & Module Organization

Chimaera couples gem5 computer simulation with Gazebo robot simulation through ROS 2 bridges.

- `gem5/`: modified simulator; Chimaera operations live in `src/chimaera/`.
- `gem5-transport/`: C++ transport headers in `include/`, implementations in `src/`, and tests in `tests/`.
- `ros/*_ws/src/`: ROS packages for wall following, ball catching, universal bridging, talker/listener, and Franka integration. Packages contain launch files, configuration, and robot assets; workspace-level `tests/` cover Python workflows.
- `containers/`: Docker setup, runners, and tests.
- `docs/`: architecture, design notes, validation records, and example reports.

## Build, Test, and Development Commands

Use Ubuntu 22.04, ROS 2 Humble, and Gazebo Fortress. Commands below run from the repository root unless specified. Source `/opt/ros/humble/setup.zsh`; use `.bash` equivalents in Bash.

- `(cd gem5 && scons build/X86/gem5.opt -j2)`: build the modified simulator.
- `(cd gem5/util/m5 && scons build/x86/out/m5 -j2)`: build custom m5 utilities and libm5.
- `make -C gem5-transport`: build transport libraries and tests.
- `ctest --test-dir gem5-transport/build --output-on-failure`: run transport tests without booting gem5.
- In `ros/wall_follow_ws`, run `colcon build --packages-select wall_follow_robot --cmake-args -DCMAKE_BUILD_TYPE=Release`, source `install/setup.zsh`, then `ros2 launch wall_follow_robot application.launch.py` to run locally.
- In that workspace, run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=benchmarking python3 -m pytest -q tests` for workflow and analysis checks.

Follow each workspace README for bridge builds, ball-catching dependencies, and deployment.

## Coding Style & Naming Conventions

Use four-space Python indentation. Format ROS C++ with `ros/.clang-format` (Google-based, two-space indentation, 100-column limit); preserve surrounding style elsewhere. Transport uses C++20; ball-catching computation uses C++17. Follow existing `snake_case` package, file, and function names and `PascalCase` C++ types. Within `gem5/`, use its Black/isort configuration and pre-commit style hooks.

## Testing Guidelines

Use pytest files named `test_*.py`, CTest transport tests, and package-local ROS tests, including GoogleTest. Enable `-DBUILD_TESTING=ON` when building ROS tests, then run `colcon test` and `colcon test-result --verbose` in the workspace. No repository-wide coverage threshold is configured. Test changed behavior and failure paths; record full simulation validation separately.

## Commit & Pull Request Guidelines

Follow history: `type(scope): imperative summary`, e.g. `fix(ball-catching): harden scoring`. Keep changes focused. PR descriptions should explain behavior, affected components, commands run, and results; link relevant issues and include screenshots for visual changes.

## Configuration & Simulation Tips

Guest disks and kernels are external artifacts. Stop gem5/QEMU before deploying to an image. Run one native Chimaera session at a time because transport socket paths are fixed.
