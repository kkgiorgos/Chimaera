"""Opt-in real Docker check of application replacement; uses synthetic ROS tools."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from images import application_image


def main():
    suffix = uuid.uuid4().hex[:12]
    name = 'chimaera-storage-base-' + suffix
    base, app = 'chimaera-storage-base:' + suffix, 'chimaera-storage-app:' + suffix
    files = {
        '/opt/ros/humble/setup.bash': '', '/opt/chimaera/ros/local_setup.bash': '',
        '/usr/local/bin/colcon': '#!/bin/bash\nset -e\nmkdir -p /opt/chimaera/ball\n'
            'cat /src/ball/packages/robot.txt > /opt/chimaera/ball/result\n'
            "echo 'export OVERLAY=installed' > /opt/chimaera/ball/local_setup.bash\n",
        '/opt/chimaera/scripts/entrypoint.sh': '#!/bin/bash\n'
            'source /opt/chimaera/ball/local_setup.bash\nexec "$@"\n',
    }
    setup = ('from pathlib import Path\nfiles = ' + repr(files) + '\n'
             'for name, content in files.items():\n'
             ' path = Path(name); path.parent.mkdir(parents=True, exist_ok=True)\n'
             ' path.write_text(content); path.chmod(0o755)\n')
    try:
        subprocess.run(['docker', 'run', '--name', name, '--network', 'none',
            '--entrypoint', 'python3', 'chimaera-supervisor:local', '-c', setup], check=True)
        subprocess.run(['docker', 'commit', name, base], check=True, stdout=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(prefix='chimaera-image-smoke-') as temporary:
            root = Path(temporary)
            (root / 'ros/franka_ws/src/franka_description').mkdir(parents=True)
            source = root / 'ros/ball_catching_ws/src/robot.txt'
            source.parent.mkdir(parents=True)
            for version in ('first', 'replacement'):
                source.write_text(version)
                application_image(root, base, app, 1)
                check = ('import os,pathlib; assert os.environ["OVERLAY"] == "installed"; '
                         'assert pathlib.Path("/opt/chimaera/ball/result").read_text() == ' + repr(version) + '; '
                         'assert not pathlib.Path("/src/ball/packages/robot.txt").exists()')
                subprocess.run(['docker', 'run', '--rm', '--network', 'none', app,
                                'python3', '-c', check], check=True)
                image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', app], text=True).strip()
                subprocess.run(['docker', 'image', 'rm', app], check=True, stdout=subprocess.DEVNULL)
                # Each application starts from the base and is removable independently.
                assert subprocess.run(['docker', 'image', 'inspect', image], capture_output=True).returncode
        print(json.dumps(dict(application_replacement=True, runtime_entrypoint=True,
                              overlay_environment=True, source_exclusion=True)))
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        subprocess.run(['docker', 'image', 'rm', app, base], capture_output=True)


if __name__ == '__main__':
    main()
