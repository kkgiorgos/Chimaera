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

## Run With Gazebo

Launch Gazebo headless, start MoveIt on top of the simulated arm, and run the same point-to-point demo:

```bash
source install/setup.bash
ros2 launch franka_moveit_examples gazebo_point_to_point_demo.launch.py
```
