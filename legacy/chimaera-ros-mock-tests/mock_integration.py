"""Run real external ROS talkers/listeners across mock-sim controller barriers."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from ament_index_python.packages import get_package_prefix


def main():
    binary, config = sys.argv[1:]
    demo = Path(get_package_prefix("demo_nodes_cpp")) / "lib/demo_nodes_cpp"
    processes = []
    with tempfile.TemporaryDirectory(prefix="bridge-mock-") as directory:
        root = Path(directory)
        endpoint = str(root / "transport.sock")

        def start(name, command, domain):
            env = dict(os.environ, ROS_DOMAIN_ID=str(domain), ROS_LOCALHOST_ONLY="1",
                       ROS_LOG_DIR=str(root / "ros_logs"))
            with (root / (name + ".log")).open("w") as log:
                process = subprocess.Popen(command, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process)
            return process

        try:
            for side, other, domain in (("host", "guest", 198), ("guest", "host", 199)):
                start(side + "_talker", [str(demo / "talker"), "--ros-args", "-r",
                                        "chatter:=/" + side + "/chatter"], domain)
                start(side + "_listener", [str(demo / "listener"), "--ros-args", "-r",
                                          "chatter:=/" + other + "/chatter"], domain)
            host = start("host_bridge", [binary, "host", endpoint, "--ros-args", "-p",
                                        "config_file:=" + config], 198)
            guest = start("guest_bridge", [binary, "guest", endpoint, "--ros-args", "-p",
                                          "config_file:=" + config], 199)
            assert host.wait(timeout=25) == 0, "host bridge failed"
            assert guest.wait(timeout=5) == 0, "guest bridge failed"
            for side in ("host", "guest"):
                text = (root / (side + "_listener.log")).read_text()
                assert text.count("I heard:") >= 2, side + " listener did not receive bridged messages"
            print("External talkers/listeners exchanged messages across mock-sim in both directions")
        except BaseException:
            for log in root.glob("*.log"):
                print(log.name + ":\n" + log.read_text(), file=sys.stderr)
            raise
        finally:
            for process in processes:
                if process.poll() is None:
                    # SIGKILL also cleans up a mock guest stopped at a barrier.
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    main()
