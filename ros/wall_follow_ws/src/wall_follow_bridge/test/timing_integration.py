"""Test real paused Fortress physics with a deterministic gem5 timing server.

Does not boot gem5. Requires exclusive access to Chimaera's data sockets.
"""
import csv
import math
import os
from pathlib import Path
import socket
import signal
import time
import subprocess
import sys
import tempfile
import threading


WORLD = '''<sdf version="1.8"><world name="wall_arena">
<physics name="physics" type="ignored"><max_step_size>0.001</max_step_size></physics>
<plugin filename="ignition-gazebo-physics-system" name="ignition::gazebo::systems::Physics"/>
</world></sdf>'''


def check(binary, config, extra, expected, cancel=False):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        timing_file = root / 'output' / 'timing.csv'
        world = root / 'world.sdf'
        world.write_text(WORLD)
        endpoint = str(root / 'timing.sock')
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(endpoint)
            server.listen()
            server.settimeout(40)
            steps = []
            first_step = threading.Event()

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
                            first_step.set()
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
                    command = [
                        binary, '--ros-args', '-p', f'timing_socket:={endpoint}',
                        '-p', f'steps:={0 if cancel else 3}', '-p', 'startup_timeout_s:=10',
                        '-p', 'status_bar:=false', '-p', f'timing_file:={timing_file}', '-p', f'config_file:={config}', *extra,
                    ]
                    if cancel:
                        process = subprocess.Popen(command, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                        try:
                            assert first_step.wait(15), 'Host never started stepping'
                            gazebo.terminate()
                            time.sleep(.2)
                            process.send_signal(signal.SIGINT)
                            stdout, _ = process.communicate(timeout=3)
                            result = subprocess.CompletedProcess(command, process.returncode, stdout)
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.wait()
                    else:
                        result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=45)
                    print(result.stdout, flush=True)
                    assert (result.returncode == 0) == (expected is None), result.stdout
                    if expected:
                        assert expected in result.stdout, result.stdout
                    elif not cancel:
                        assert steps == [100_000_000_000] * 3, steps
                        with timing_file.open() as stream:
                            rows = list(csv.DictReader(stream))
                        assert len(rows) == 3
                        for row in rows:
                            values = {k: float(v) for k, v in row.items()}
                            assert all(math.isfinite(v) and v >= 0 for v in values.values())
                            assert values['sim_seconds'] == .1
                            assert values['gem5_sim_seconds'] == .1
                            phases = sum(values[k] for k in ('gem5_wall_seconds', 'gazebo_wall_seconds',
                                         'other_wall_seconds', 'pacing_wall_seconds'))
                            assert math.isclose(phases, values['wall_seconds'], abs_tol=1e-8)
                            assert values['other_wall_seconds'] >= .04
                            assert values['pacing_wall_seconds'] == 0.0
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

    check(sys.argv[1], sys.argv[2], [], None, cancel=True)
