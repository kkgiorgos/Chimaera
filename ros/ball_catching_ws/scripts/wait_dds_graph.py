#!/usr/bin/env python3
"""Wait for the guest's internal robot graph before admitting the first frame.

The coordinator deliberately has no routed DDS participant. This separate
startup process joins the application's transport, then releases all its DDS
resources and remains asleep for the session supervisor. No receive timer or
polling process remains active during the experiment.
"""
import argparse
from pathlib import Path
import signal
import time

import rclpy


TOPICS = ('/robot/ball_state', '/robot/ball_geometry', '/robot/joint_trajectory')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready-file', required=True, type=Path)
    parser.add_argument('--timeout', required=True, type=float)
    args = parser.parse_args()
    rclpy.init()
    node = rclpy.create_node('chimaera_ball_dds_readiness')
    deadline = time.monotonic() + args.timeout
    try:
        while True:
            missing = [topic for topic in TOPICS if not node.count_publishers(topic)
                       or not node.count_subscribers(topic)]
            if not missing:
                break
            if not rclpy.ok() or time.monotonic() >= deadline:
                raise RuntimeError('Timed out waiting for local DDS graph: ' + ', '.join(missing))
            rclpy.spin_once(node, timeout_sec=.05)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    # Publish the marker only after our temporary participant has been removed.
    args.ready_file.write_text('local robot publishers and subscribers discovered\n')
    print('Local ball-catching DDS graph ready before workbegin', flush=True)
    while True:
        signal.pause()


if __name__ == '__main__':
    main()
