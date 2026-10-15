import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from transport import SSH


def test_deleting_experiment_interrupts_ssh_fetch_and_reaps_process(tmp_path):
    ssh = SSH()
    cancelled = threading.Event()
    processes = []
    real_popen = subprocess.Popen

    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    timer = threading.Timer(0.1, cancelled.set)
    timer.start()
    try:
        with patch.object(ssh, 'base', return_value=[sys.executable, '-c', 'import time; time.sleep(60)']), \
             patch('transport.subprocess.Popen', side_effect=start):
            with pytest.raises(RuntimeError, match='cancelled'):
                ssh.fetch({}, '/remote', tmp_path / 'archive', cancel=cancelled)
    finally:
        timer.cancel()
    assert len(processes) == 1
    assert processes[0].poll() is not None
    # Input bundles should not be downloaded again with each case's results.
    assert '--exclude=./suite/guest.tar.gz' in processes[0].args[-1]
