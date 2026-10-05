import json
from pathlib import Path
import rclpy
from rclpy.executors import ExternalShutdownException


def seconds(stamp):
    return stamp.sec + stamp.nanosec * 1e-9


class Recorder:
    def __init__(self, directory, name):
        self.file = None
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            self.file = open(Path(directory) / (name + '.jsonl'), 'w', buffering=1)

    def write(self, **record):
        if self.file:
            self.file.write(json.dumps(record) + '\n')

    def close(self):
        if self.file:
            self.file.close()


def run(factory):
    rclpy.init()
    node = factory()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if hasattr(node, 'record'):
            node.record.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
