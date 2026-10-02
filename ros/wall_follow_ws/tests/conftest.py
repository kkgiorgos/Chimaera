"""Make the shared application world generator available to source-tree tests."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/wall_follow_robot'))
