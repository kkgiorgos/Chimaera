"""Test real paused Fortress physics with a deterministic gem5 timing server.

Does not boot gem5. Requires exclusive access to Chimaera's data sockets.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading


WORLD = '''<sdf version="1.8"><world name="wall_arena">
<physics name="physics" type="ignored"><max_step_size>0.001</max_step_size></physics>
<plugin filename="ignition-gazebo-physics-system" name="ignition::gazebo::systems::Physics"/>
</world></sdf>'''


def check(binary, config, extra, expected):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        world = root / 'world.sdf'
        world.write_text(WORLD)
        endpoint = str(root / 'timing.sock')
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(endpoint)
            server.listen()
            server.settimeout(40)
            steps = []

            def serve():
                tick = 0
                while True:
                    conn, _ = server.accept()
                    with conn, conn.makefile() as incoming:
                        line = incoming.readline().strip()
                        if line == 'STATUS':
                            reply = f'PAUSED {tick}'
                        elif line.startswith('STEP_TICKS '):
                            amount = int(line.split()[1])
                            steps.append(amount)
                            end = tick + amount
                            reply = f'OK {tick} {end}'
                            tick = end
                        elif line == 'QUIT':
                            conn.sendall(b'BYE\n')
                            break
                        else:
                            raise AssertionError(line)
                        conn.sendall((reply + '\n').encode())

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            env = dict(os.environ, IGN_PARTITION='wall_follow_test_' + str(os.getpid()),
                       ROS_DOMAIN_ID='196', ROS_LOCALHOST_ONLY='1', ROS_LOG_DIR=str(root / 'logs'))
            with (root / 'gazebo.log').open('w') as log:
                gazebo = subprocess.Popen(['ign', 'gazebo', '-s', str(world)], env=env,
                                          stdout=log, stderr=subprocess.STDOUT)
                try:
                    result = subprocess.run([
                        binary, '--ros-args', '-p', f'timing_socket:={endpoint}',
                        '-p', 'steps:=3', '-p', 'startup_timeout_s:=10',
                        '-p', 'status_bar:=false', '-p', f'config_file:={config}', *extra,
                    ], env=env, text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=45)
                    print(result.stdout, flush=True)
                    assert (result.returncode == 0) == (expected is None), result.stdout
                    if expected:
                        assert expected in result.stdout, result.stdout
                    else:
                        assert steps == [100_000_000_000] * 3, steps
                    thread.join(timeout=2)
                    assert not thread.is_alive(), 'gem5 did not receive QUIT'
                finally:
                    gazebo.terminate()
                    try:
                        gazebo.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        gazebo.kill()
                        gazebo.wait()


if __name__ == '__main__':
    check(sys.argv[1], sys.argv[2], [], None)
    check(sys.argv[1], sys.argv[2], ['-p', 'physics_step_ns:=2000000'], 'Gazebo step mismatch')
    check(sys.argv[1], sys.argv[2], ['-p', 'physics_step_ns:=1500000'],
          'integral number of physics steps')
