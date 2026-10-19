"""Exercise real ROS traffic, its wire shapes, and the buffered export protocol."""
import csv
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time

import rclpy
from rclpy.qos import qos_profile_sensor_data
from rclpy.serialization import serialize_message
from sensor_msgs.msg import Image, JointState
from rosgraph_msgs.msg import Clock


def check(binary):
    rclpy.init()
    node = rclpy.create_node('latency_wire_shape_check')
    received, subscriptions = {}, []
    for name, kind in [('left_image',Image), ('right_image',Image),
                       ('joint_states',JointState), ('effort_command',JointState), ('clock',Clock)]:
        subscriptions.append(node.create_subscription(kind, '/latency/ball/'+name+'/host',
            lambda message, name=name: received.setdefault(name,message), qos_profile_sensor_data))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        clock = root/'clock.bin'
        clock.write_bytes(struct.pack('<QQ',0x434c42434c4f434b,2**64-1))
        processes, logs = [], []
        try:
            for side in ('host','guest'):
                parameters = dict(workload='ball_catching', side=side, bridged=False,
                    clock_kind='shared', clock_file=str(clock), physics_clock_file=str(clock),
                    ready_file=str(root/(side+'-ready')), output_file=str(root/'events.csv'),
                    done_file=str(root/'complete'), warmup_ns=0, duration_ns=100000000, drain_ns=20000000,
                    payload_bytes=921600, rate_hz=90., reliability='best_effort', depth=1)
                # Scalar YAML with JSON quoting, matching the public runner.
                import json
                config = root/(side+'.yaml')
                config.write_text('/**:\n  ros__parameters:\n'+''.join(
                    f'    {key}: {json.dumps(value)}\n' for key,value in parameters.items()))
                log = (root/(side+'.log')).open('w')
                logs.append(log)
                processes.append(subprocess.Popen([binary,'--ros-args','--params-file',str(config)],
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True))
            deadline = time.monotonic()+15
            while not (all((root/(side+'-ready')).exists() for side in ('host','guest'))
                       and all(node.count_publishers(sub.topic_name)>0 for sub in subscriptions)):
                rclpy.spin_once(node,timeout_sec=.02)
                assert all(p.poll() is None for p in processes), 'An endpoint exited'
                assert time.monotonic()<deadline, 'Endpoint discovery timed out'
            until = time.monotonic()+.2
            while time.monotonic()<until:
                rclpy.spin_once(node,timeout_sec=.01)
            with clock.open('r+b') as stream:
                stream.seek(8)
                stream.write(struct.pack('<Q',0))
            simulation_time, advance_at = 0, time.monotonic()+.1
            # A tap receiving one best-effort image does not mean the separate
            # endpoint received that fragmented sample. Exercise the full
            # source window before checking the endpoint's buffered receipts.
            while len(received)<5 or simulation_time<90000000:
                rclpy.spin_once(node,timeout_sec=.05)
                # Best-effort images may drop individual fragmented samples.
                # Release further normal samples rather than assuming lossless DDS.
                if time.monotonic()>=advance_at and simulation_time<90000000:
                    simulation_time += 10000000
                    with clock.open('r+b') as stream:
                        stream.seek(8)
                        stream.write(struct.pack('<Q',simulation_time))
                    advance_at = time.monotonic()+.1
                assert all(p.poll() is None for p in processes), 'An endpoint exited'
                assert time.monotonic()<deadline, 'Typed traffic discovery timed out: '+str(list(received))
            for name in ('left_image','right_image'):
                image = received[name]
                assert (image.width,image.height,image.step,image.encoding)==(640,480,1920,'rgb8')
                assert len(image.data)==921600 and image.data[0]==0xa5
                assert image.header.stamp.sec==0 and image.header.stamp.nanosec<100000000
            assert len(serialize_message(received['left_image']))==921656
            assert len(serialize_message(received['right_image']))==921660
            names = ['fr3_joint'+str(i) for i in range(1,8)]
            feedback, command = received['joint_states'],received['effort_command']
            assert list(feedback.name)==names and list(command.name)==names
            assert (len(feedback.position),len(feedback.velocity),len(feedback.effort))==(7,7,0)
            assert (len(command.position),len(command.velocity),len(command.effort))==(0,0,7)
            assert len(serialize_message(feedback))==268
            assert len(serialize_message(command))==212
            assert len(serialize_message(received['clock']))==12
            # Keep the clock frozen until both applications have processed the
            # sample, then enter drain/export without introducing more sources.
            until = time.monotonic()+.1
            while time.monotonic()<until:
                rclpy.spin_once(node,timeout_sec=.01)
            with clock.open('r+b') as stream:
                stream.seek(8)
                stream.write(struct.pack('<Q',120000000))
            while not (root/'complete').exists():
                rclpy.spin_once(node,timeout_sec=.02)
                assert time.monotonic()<deadline, 'Buffered event export timed out'
            with (root/'events.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            assert len(rows)>=10, rows
            assert {r['stream'] for r in rows}==set(received)
            for name in received:
                assert any(r['stream']==name and r['event']=='send' for r in rows)
                assert any(r['stream']==name and r['event']=='receive' for r in rows)
        except BaseException:
            for log in logs:
                log.flush()
                print(Path(log.name).read_text(),file=sys.stderr)
            raise
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGINT)
            for process in processes:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL)
                    process.wait()
            for log in logs:
                log.close()
            node.destroy_node()
            rclpy.shutdown()


if __name__=='__main__':
    check(sys.argv[1])
