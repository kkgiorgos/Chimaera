"""Stage the wall-follow guest using the shared Chimaera session implementation."""

from pathlib import Path
import shutil
import tempfile

from chimaera_ros.manifest import load
from chimaera_ros.runner import stage


def stage_session(sdk, payload):
    sdk, payload = Path(sdk), Path(payload)
    session = load(sdk / 'ros/share/wall_follow_bridge/config/session.json')
    session['deploy']['install_trees'] = [
        {'source': str(sdk / 'ros'), 'destination': 'app'}]
    with tempfile.TemporaryDirectory(prefix='chimaera-guest-stage-') as temporary:
        root = stage(session, Path(temporary) / 'root', package_prefix=sdk / 'ros')
        destination = payload / 'opt/chimaera/wall_follow'
        shutil.copytree(root, destination, symlinks=True)
    # These host executables would pull Gazebo libraries into the guest.
    for name in ('host_bridge', 'gazebo_host_bridge', 'validate_routes'):
        (destination / 'app/lib/chimaera_ros_bridge' / name).unlink()
    return destination
