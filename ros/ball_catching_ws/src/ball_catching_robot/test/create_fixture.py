"""Build native test input from the same upstream FR3/cup description as Gazebo."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ball_catching_sim'))
from ball_catching_sim.scene import robot_urdf

Path(sys.argv[1]).write_text(robot_urdf())
