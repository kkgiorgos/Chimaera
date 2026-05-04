# franka_moveit_examples

Sample `MoveGroupInterface` demo for a simulated Franka FR3 arm.

## Run

Build the package and the FR3 MoveIt config:

```bash
colcon build --packages-select franka_fr3_moveit_config franka_moveit_examples
```

Launch the fake-hardware MoveIt stack and automatically run the demo:

```bash
source install/setup.bash
ros2 launch franka_moveit_examples point_to_point_demo.launch.py
```

The node reads the current end-effector pose, moves to a nearby point A, and then to point B.
When the demo process exits, the launch shuts down the rest of the stack. Task timings are
appended to `point_to_point_execution_times.csv` by default:

```bash
ros2 launch franka_moveit_examples point_to_point_demo.launch.py execution_log_file:=/tmp/ptp_times.csv
```

The measured task duration includes Cartesian path planning and trajectory execution. To repeat
the A-to-B run multiple times, pass `point_to_point_repetitions`; the demo returns to point A
between repetitions:

```bash
ros2 launch franka_moveit_examples gazebo_point_to_point_demo.launch.py point_to_point_repetitions:=5 cycle_pause_seconds:=1.0 execution_log_file:=/tmp/ptp_times.csv
```

## Run With Gazebo

Launch Gazebo headless, start MoveIt on top of the simulated arm, and run the same point-to-point demo:

```bash
source install/setup.bash
ros2 launch franka_moveit_examples gazebo_point_to_point_demo.launch.py
```
