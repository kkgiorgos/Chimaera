"""Check sequencing and real report generation with synthetic suite results."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]


@pytest.fixture
def helper(tmp_path):
    workspace = tmp_path / 'workspace with spaces'
    scripts = workspace / 'scripts'
    scripts.mkdir(parents=True)
    script = scripts / 'run_timing_extreme.sh'
    shutil.copy2(WORKSPACE / 'scripts/run_timing_extreme.sh', script)
    # Coverage imports the actual eligible() function, which cannot launch a run.
    (scripts / 'run_experiments.py').symlink_to(WORKSPACE / 'scripts/run_experiments.py')
    (workspace / 'install').mkdir()
    (workspace / 'install/setup.bash').write_text('# Isolated test environment.\n')
    (workspace / 'experiments').mkdir()
    for profile in ('large', 'tiny'):
        name = f'timing_extreme_{profile}.json'
        shutil.copy2(WORKSPACE / 'experiments' / name, workspace / 'experiments' / name)
    executables = tmp_path / 'bin'
    executables.mkdir()
    stub = executables / 'python3'
    stub.write_text(f'''#!{sys.executable}
import json, os
from pathlib import Path
import sys

source = Path(os.environ['CHIMAERA_TEST_WORKSPACE'])
name = Path(sys.argv[1]).name
trace = Path(os.environ['CHIMAERA_TEST_TRACE'])
if name == 'run_gem5_experiments.py':
    output = Path(sys.argv[sys.argv.index('--output') + 1])
    profile = output.name
    with trace.open('a') as stream:
        stream.write(json.dumps(dict(stage=profile, argv=sys.argv[2:])) + '\\n')
    if '--dry-run' in sys.argv:
        sys.exit(0)
    status = int(os.environ.get('CHIMAERA_TEST_LARGE_STATUS', '0')) if profile == 'large' else 0
    if status > 1:
        sys.exit(status)
    sys.path[:0] = [str(source / 'tests'), str(source / 'scripts'), str(source / 'benchmarking')]
    from test_comparison import create_run, write_timing
    from run_experiments import make_plan
    config = Path(sys.argv[sys.argv.index('--config') + 1])
    plan = make_plan(json.loads(config.read_text()), 'timing-extreme-poc', gem5=True)
    for index, run in enumerate(plan['runs']):
        path = output / 'runs' / run['id'] / 'attempt_001'
        if '--resume' in sys.argv and path.exists():
            continue
        create_run(path, rate=10., architecture='synthetic-test')
        experiment = json.loads((path / 'experiment.json').read_text())
        experiment['hardware'] = {{key: run['parameters'][key] for key in
            ('cpu_type', 'cpu_clock', 'num_cores', 'l1i_size', 'l1d_size', 'l2_size', 'l1_assoc', 'l2_assoc')}}
        (path / 'experiment.json').write_text(json.dumps(experiment))
        (path / 'attempt.json').write_text(json.dumps(dict(status='failed' if status and index == 0 else 'completed')))
        write_timing(path)
    sys.exit(status)
elif name == 'compare_experiments.py':
    with trace.open('a') as stream:
        stream.write(json.dumps(dict(stage='comparison', argv=sys.argv[2:])) + '\\n')
    os.execv(sys.executable, [sys.executable, str(source / 'scripts/compare_experiments.py'), *sys.argv[2:]])
elif sys.argv[1] == '-':
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
else:
    raise RuntimeError('Unexpected command in isolated helper test')
''')
    stub.chmod(0o755)
    ros = executables / 'ros2'
    ros.write_text('#!/bin/sh\nexit 99\n')
    ros.chmod(0o755)
    trace = tmp_path / 'trace.jsonl'
    environment = dict(os.environ, ROS_DISTRO='humble',
                       PATH=str(executables) + os.pathsep + os.environ['PATH'],
                       CHIMAERA_TEST_WORKSPACE=str(WORKSPACE), CHIMAERA_TEST_TRACE=str(trace))
    output = tmp_path / 'results with spaces'
    return script, output, environment, trace


def invoke(helper, *arguments, **overrides):
    script, output, environment, _ = helper
    return subprocess.run([str(script), '--output', str(output), *arguments],
                          env=dict(environment, **overrides), capture_output=True, text=True)


def stages(helper):
    return [json.loads(line) for line in helper[3].read_text().splitlines()]


def test_sequence_generates_four_case_plots_and_dashboard(helper):
    result = invoke(helper)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = stages(helper)
    assert [call['stage'] for call in calls] == ['large', 'tiny', 'comparison']
    assert all('--no-plot' in call['argv'] for call in calls[:2])
    assert 'Comparison covers 4/4 completed cases.' in result.stdout
    comparison = helper[1] / 'comparison'
    for name in ('comparison.png', 'metrics.png', 'timing.png', 'configuration.png', 'dashboard.html'):
        assert (comparison / name).stat().st_size > 1000
    assert len(json.loads((comparison / 'included_runs.json').read_text())) == 4
    assert len(json.loads((comparison / 'summary.json').read_text())) == 4
    if shutil.which('node'):
        subprocess.run(['node', str(WORKSPACE / 'tests/dashboard_smoke.cjs'),
                        str(comparison / 'dashboard.html')], check=True, capture_output=True)
    assert invoke(helper).returncode == 2  # Existing output must not be overwritten.
    resumed = invoke(helper, '--resume')
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert len(list(helper[1].rglob('attempt.json'))) == 4
    assert all('--resume' in call['argv'] for call in stages(helper)[3:5])
    plotted = invoke(helper, '--plot-only')
    assert plotted.returncode == 0, plotted.stdout + plotted.stderr
    assert [call['stage'] for call in stages(helper)[6:]] == ['comparison']


def test_failed_large_case_still_runs_tiny_and_reports_partial_coverage(helper):
    result = invoke(helper, CHIMAERA_TEST_LARGE_STATUS='1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert [call['stage'] for call in stages(helper)] == ['large', 'tiny', 'comparison']
    assert 'Comparison covers 3/4 completed cases.' in result.stdout
    assert (helper[1] / 'comparison/dashboard.html').is_file()


def test_interrupted_suite_does_not_start_another_suite(helper):
    result = invoke(helper, CHIMAERA_TEST_LARGE_STATUS='130')
    assert result.returncode == 130
    assert [call['stage'] for call in stages(helper)] == ['large']


def test_dry_run_does_not_generate_results_or_comparison(helper):
    result = invoke(helper, '--dry-run')
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call['stage'] for call in stages(helper)] == ['large', 'tiny']
    assert not helper[1].exists()
