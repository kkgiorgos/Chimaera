"""Verify setup, execution, reporting, resume, and development command wiring."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))


def load(name):
    spec = importlib.util.spec_from_file_location('wall_follow_' + name, DIRECTORY / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_container_suite_setup_report_and_resume(tmp_path, monkeypatch):
    suite = load('suite')
    config = tmp_path / 'experiment.json'
    config.write_text(json.dumps(dict(repetitions=1, fixed=dict(duration=1))))
    output = tmp_path / 'results'
    events = []
    def prepare(image, overlay, jobs, rebuild):
        events.append(('build', image))
        overlay.mkdir()
    def script(path, *args):
        events.append(('script', path.name))
        if path.name == 'configure.py':
            import configure
            configure.configure(SimpleNamespace(config=config, architecture='local', local=True,
                overlay=Path(args[args.index('--overlay') + 1]), guest_assets=None,
                output=Path(args[args.index('--output') + 1]), image='test'))
    def call(command):
        events.append(('execute', 'report' if command[0] == 'docker' else Path(command[1]).name, command))
        return 0
    monkeypatch.setattr(suite, 'prepare', prepare)
    monkeypatch.setattr(suite, 'run_script', script)
    monkeypatch.setattr(suite.subprocess, 'call', call)
    argv = ['suite', '--local', '--image', 'test', '--config', str(config), '--output', str(output)]
    monkeypatch.setattr(sys, 'argv', argv)
    assert suite.main() == 0
    assert [(event[0], event[1]) for event in events] == [
        ('build', 'test'), ('script', 'configure.py'), ('execute', 'run.py'), ('execute', 'report')]
    runner = events[2][2]
    assert Path(runner[runner.index('--config') + 1]).is_file()
    assert output not in Path(runner[runner.index('--config') + 1]).parents
    events.clear()
    monkeypatch.setattr(sys, 'argv', [*argv, '--resume'])
    assert suite.main() == 0
    assert [event[1] for event in events] == ['run.py', 'report']
    assert '--resume' in events[0][2]
    config.write_text(json.dumps(dict(fixed=dict(duration=2))))
    events.clear()
    with pytest.raises(SystemExit):
        suite.main()
    assert not events


def test_gem5_suite_prepares_guest_and_reports_failure(tmp_path, monkeypatch):
    suite = load('suite')
    config = tmp_path / 'experiment.json'
    config.write_text(json.dumps(dict(repetitions=1)))
    output = tmp_path / 'results'
    steps = []
    monkeypatch.setattr(suite, 'prepare', lambda *args: steps.append('build'))
    monkeypatch.setattr(suite, 'run_script', lambda path, *args: steps.append(path.name))
    def call(command):
        steps.append('report' if command[0] == 'docker' else Path(command[1]).name)
        return 1 if command[0] != 'docker' else 0
    monkeypatch.setattr(suite.subprocess, 'call', call)
    monkeypatch.setattr(sys, 'argv', ['suite', '--config', str(config), '--output', str(output)])
    assert suite.main() == 1
    assert steps == ['build', 'prepare-guest.py', 'configure.py', 'run.py', 'report']


def test_dev_headless_runs_application_without_benchmark(tmp_path):
    dev = load('dev')
    command = dev.command(SimpleNamespace(overlay=tmp_path, image='test', shell=False,
                                         headless=True, launch_args=['lidar_hz:=10']))
    assert command[-6:] == ['ros2', 'launch', 'wall_follow_robot', 'application.launch.py',
                            'gui:=false', 'lidar_hz:=10']
    assert '--device' not in command
    assert not any('collector' in arg or 'gem5.opt' in arg for arg in command)


def test_dev_gui_passes_display_and_authority(tmp_path, monkeypatch):
    dev = load('dev')
    authority = tmp_path / 'authority'
    authority.touch()
    monkeypatch.setenv('DISPLAY', ':7')
    monkeypatch.setenv('XAUTHORITY', str(authority))
    command = dev.command(SimpleNamespace(overlay=tmp_path, image='test', shell=False,
                                         headless=False, launch_args=[]))
    assert 'DISPLAY=:7' in command and 'gui:=true' in command
    assert f'type=bind,source={authority},target=/tmp/chimaera-xauthority,readonly' in command


def test_dev_gui_without_display_is_actionable(tmp_path, monkeypatch):
    dev = load('dev')
    monkeypatch.delenv('DISPLAY', raising=False)
    with pytest.raises(ValueError, match='--headless'):
        dev.command(SimpleNamespace(overlay=tmp_path, image='test', shell=False,
                                    headless=False, launch_args=[]))
