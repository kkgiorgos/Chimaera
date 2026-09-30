import importlib.util
from contextlib import nullcontext
import fcntl
import json
import signal
from pathlib import Path
import subprocess
import sys

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('parallel_suite', DIRECTORY / 'run-suite.py')
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


def test_partition_keeps_every_case_and_repetition():
    plan = suite.make_plan(dict(repetitions=3, sweep=dict(control_hz=[10, 10, 20])), 'cpu')
    for limit in (1, 2, 4, 20):
        shards = suite.partition(plan, limit)
        assigned = [run_id for shard in shards for run_id in shard['run_ids']]
        assert len(shards) == min(limit, 9)
        assert len(assigned) == len(set(assigned)) == 9
        assert set(assigned) == {run['id'] for run in plan['runs']}
        for shard in shards:
            assert suite.select_runs(plan, shard['run_ids'])
    with pytest.raises(ValueError):
        suite.partition(plan, 0)


def test_dry_run_needs_no_docker_and_writes_nothing(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(repetitions=2, sweep=dict(control_hz=[10, 20]))))
    output = tmp_path / 'output'
    result = subprocess.run([sys.executable, str(DIRECTORY / 'run-suite.py'), '--config', str(config),
        '--architecture', 'cpu', '--workers', '3', '--output', str(output), '--dry-run'],
        capture_output=True, text=True, check=True)
    printed = json.loads(result.stdout)
    assert len(printed['plan']['runs']) == 4
    assert [len(s['run_ids']) for s in printed['shards']] == [2, 1, 1]
    assert not output.exists()


@pytest.fixture
def results(tmp_path):
    sys.path.insert(0, str(DIRECTORY.parents[1] / 'ros/wall_follow_ws/tests'))
    from test_comparison import create_run

    plan = suite.make_plan(dict(repetitions=2, sweep=dict(control_hz=[10, 20])), 'cpu')
    shards = suite.partition(plan, 2)
    for shard in shards:
        output = tmp_path / 'workers' / shard['name'] / 'suite'
        output.mkdir(parents=True)
        suite.write_json(output / 'suite.json', dict(plan, selected_run_ids=shard['run_ids']))
        for run_id in shard['run_ids']:
            attempt = output / 'runs' / run_id / 'attempt_001'
            rate = next(run['parameters']['control_hz'] for run in plan['runs'] if run['id'] == run_id)
            create_run(attempt, rate=rate, architecture='cpu')
            suite.write_json(attempt / 'attempt.json', dict(status='completed'))
    return tmp_path, plan, shards


def test_collection_retains_failures_without_counting_them(results):
    root, plan, shards = results
    parent = root / 'workers/worker-001/suite/runs/case_001_rep_01'
    failed = parent / 'attempt_002'
    failed.mkdir()
    suite.write_json(failed / 'attempt.json', dict(status='failed'))
    destination = root / 'collection'
    coverage = suite.collect(root, plan, shards, destination)
    assert not coverage['missing'] and not coverage['errors'] and not coverage['duplicates']
    assert set(coverage['selected']) == {run['id'] for run in plan['runs']}
    assert (destination / 'runs/case_001_rep_01/attempt_002/attempt.json').is_file()
    assert len(json.loads((destination / 'selected_runs.json').read_text())) == 4


def test_collection_flags_missing_duplicate_and_wrong_assignment(results):
    root, plan, shards = results
    original = root / 'workers/worker-001/suite/runs/case_001_rep_01/attempt_001'
    suite.shutil.copytree(original, original.parent / 'attempt_002')
    missing = root / 'workers/worker-002/suite/runs/case_001_rep_02/attempt_001'
    suite.write_json(missing / 'attempt.json', dict(status='failed'))
    coverage = suite.collect(root, plan, shards, root / 'collection')
    assert coverage['duplicates'] == ['case_001_rep_01']
    assert coverage['missing'] == ['case_001_rep_02']
    assert len(coverage['selected']) == 3
    saved = root / 'workers/worker-001/suite/suite.json'
    wrong = json.loads(saved.read_text())
    wrong['selected_run_ids'] = shards[1]['run_ids']
    suite.write_json(saved, wrong)
    bad = suite.collect(root, plan, shards, root / 'collection-again')
    assert bad['errors'] and set(shards[0]['run_ids']) <= set(bad['missing'])


def test_successful_report_that_omits_a_run_is_rejected(tmp_path):
    paths = [str(tmp_path / 'one'), str(tmp_path / 'two')]
    coverage = dict(selected=dict(one=paths[0], two=paths[1]))
    suite.write_json(tmp_path / 'included_runs.json', paths)
    suite.write_json(tmp_path / 'per_run_summary.json', [dict(path=paths[0])])
    (tmp_path / 'dashboard.html').write_text('<html></html>')
    with pytest.raises(ValueError, match='omitted or duplicated'):
        suite.verify_report(coverage, tmp_path)
    suite.write_json(tmp_path / 'per_run_summary.json', [dict(path=p) for p in paths])
    assert suite.verify_report(coverage, tmp_path) == paths


def test_resume_rejects_changed_identity_before_launch(tmp_path, monkeypatch):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(repetitions=1)))
    root = tmp_path / 'output'
    root.mkdir()
    args = suite.parser().parse_args(['--config', str(config), '--output', str(root),
                                    '--architecture', 'cpu', '--local', '--resume'])
    content, plan, shards = suite.prepare(args)
    monkeypatch.setattr(suite, 'execution', lambda *_: dict(image_id='new-image'))
    suite.write_json(root / 'orchestration.json', dict(identity=dict(image_id='old-image')))
    with pytest.raises(ValueError, match='cannot resume'):
        suite.run(args, content, plan, shards)
    assert not (root / 'workers').exists()


def test_publication_keeps_previous_generation_on_failure(tmp_path, monkeypatch):
    previous = tmp_path / 'reports/old'
    previous.mkdir(parents=True)
    (previous / 'dashboard.html').write_text('complete')
    suite.publish(tmp_path, 'comparison', previous)
    incomplete = tmp_path / 'reports/new'
    incomplete.mkdir()
    assert (tmp_path / 'comparison/dashboard.html').read_text() == 'complete'
    (incomplete / 'dashboard.html').write_text('new complete')
    original = Path.replace

    def fail_replace(path, target):
        if path.name == '.comparison.tmp':
            raise OSError('interrupted publication')
        return original(path, target)

    monkeypatch.setattr(Path, 'replace', fail_replace)
    with pytest.raises(OSError, match='interrupted publication'):
        suite.publish(tmp_path, 'comparison', incomplete)
    assert (tmp_path / 'comparison/dashboard.html').read_text() == 'complete'
    monkeypatch.setattr(Path, 'replace', original)
    suite.publish(tmp_path, 'comparison', incomplete)
    assert (tmp_path / 'comparison/dashboard.html').read_text() == 'new complete'


def test_live_worker_lock_refuses_resume_or_collection(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('must refuse a live writer before inspecting Docker')

    monkeypatch.setattr(suite.worker, 'docker', unexpected)
    with (tmp_path / '.worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='still owns'):
            with suite.worker.idle_output(tmp_path):
                pass


def test_failed_shard_still_gathers_compares_and_resumes(tmp_path, monkeypatch):
    workspace = DIRECTORY.parents[1] / 'ros/wall_follow_ws'
    fake = tmp_path / 'fake-worker.py'
    fake.write_text(f'''import json, pathlib, sys
sys.path.insert(0, {str(workspace / 'scripts')!r})
sys.path.insert(0, {str(workspace / 'tests')!r})
from run_experiments import make_plan, eligible
from test_comparison import create_run
output, config, selector = map(pathlib.Path, sys.argv[1:4])
failed_id = sys.argv[4] if len(sys.argv) > 4 else None
plan = make_plan(json.loads(config.read_text()), 'cpu')
ids = json.loads(selector.read_text())
target = output / 'suite'
target.mkdir(parents=True, exist_ok=True)
(target / 'suite.json').write_text(json.dumps(dict(plan, selected_run_ids=ids)))
failure = False
for run in plan['runs']:
    if run['id'] not in ids:
        continue
    parent = target / 'runs' / run['id']
    if any(eligible(p) for p in parent.glob('attempt_*')):
        continue
    attempt = parent / f"attempt_{{len(list(parent.glob('attempt_*'))) + 1:03d}}"
    create_run(attempt, rate=run['parameters']['control_hz'], architecture='cpu')
    failed = run['id'] == failed_id
    (attempt / 'attempt.json').write_text(json.dumps(dict(status='failed' if failed else 'completed')))
    failure |= failed
(output / 'worker.json').write_text('{{}}')
sys.exit(1 if failure else 0)
''')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(repetitions=2, sweep=dict(control_hz=[10, 20]))))
    root = tmp_path / 'result'
    args = suite.parser().parse_args(['--config', str(config), '--output', str(root),
        '--architecture', 'cpu', '--local', '--warmup', '0'])
    content, plan, shards = suite.prepare(args)
    monkeypatch.setattr(suite, 'execution', lambda *_: dict(image_id='test-image'))
    monkeypatch.setattr(suite.worker, 'idle_output', lambda *_: nullcontext())
    failing = True

    def fake_command(options, output, shard, image):
        result = [sys.executable, str(fake), str(output / 'workers' / shard['name']),
                  str(output / 'inputs/config.json'), str(output / 'inputs' / (shard['name'] + '.json'))]
        return result + ['case_002_rep_02'] if failing else result

    monkeypatch.setattr(suite, 'command', fake_command)
    assert suite.run(args, content, plan, shards) == 1
    state = json.loads((root / 'orchestration.json').read_text())
    assert state['status'] == 'partial'
    assert state['coverage']['missing'] == ['case_002_rep_02']
    assert len(json.loads((root / 'comparison/per_run_summary.json').read_text())) == 3
    assert 'Partial suite: 3/4' in (root / 'comparison/dashboard.html').read_text()
    args.resume = True
    failing = False
    assert suite.run(args, content, plan, shards) == 0
    assert json.loads((root / 'orchestration.json').read_text())['status'] == 'completed'
    assert len(json.loads((root / 'comparison/per_run_summary.json').read_text())) == 4
    assert len(list((root / 'workers').rglob('attempt.json'))) == 5


@pytest.fixture
def resuming_suite(results, monkeypatch):
    root, plan, shards = results
    config = root / 'original-config.json'
    config.write_text(json.dumps(dict(repetitions=2, sweep=dict(control_hz=[10, 20]))))
    args = suite.parser().parse_args(['--config', str(config), '--output', str(root),
        '--architecture', 'cpu', '--local', '--warmup', '0', '--resume'])
    content, actual, assignments = suite.prepare(args)
    assert actual == plan and assignments == shards
    monkeypatch.setattr(suite, 'execution', lambda *_: dict(image_id='test-image'))
    monkeypatch.setattr(suite.worker, 'idle_output', lambda *_: nullcontext())
    monkeypatch.setattr(suite, 'command', lambda *_: [sys.executable, '-c', ''])
    suite.write_json(root / 'orchestration.json', dict(schema_version=1, status='completed',
                    identity=dict(image_id='test-image', plan=plan, shards=shards)))
    previous = root / 'reports/previous'
    previous.mkdir(parents=True)
    (previous / 'dashboard.html').write_text('previous comparison')
    suite.publish(root, 'comparison', previous)
    return root, args, content, plan, shards


def test_interrupted_invalidation_cannot_advertise_previous_success(resuming_suite, monkeypatch):
    root, args, content, plan, shards = resuming_suite
    original = Path.unlink

    def fail_unlink(path, *positional, **keywords):
        if path == root / 'gathered':
            raise OSError('interrupted invalidation')
        return original(path, *positional, **keywords)

    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    with pytest.raises(OSError, match='interrupted invalidation'):
        suite.run(args, content, plan, shards)
    assert json.loads((root / 'orchestration.json').read_text())['status'] == 'failed'
    assert not (root / 'comparison').exists()


@pytest.mark.parametrize('moment', ['verification', 'publication', 'final_record'])
def test_late_signal_cannot_publish_success(resuming_suite, monkeypatch, moment):
    root, args, content, plan, shards = resuming_suite
    if moment == 'verification':
        original = suite.verify_report

        def cancel(*values):
            result = original(*values)
            signal.raise_signal(signal.SIGINT)
            return result

        monkeypatch.setattr(suite, 'verify_report', cancel)
    elif moment == 'publication':
        original = suite.publish

        def cancel(directory, name, target):
            if name == 'comparison':
                signal.raise_signal(signal.SIGINT)
            return original(directory, name, target)

        monkeypatch.setattr(suite, 'publish', cancel)
    else:
        original = suite.write_json

        def cancel(path, value):
            if path == root / 'orchestration.json' and value.get('status') == 'completed':
                signal.raise_signal(signal.SIGINT)
            return original(path, value)

        monkeypatch.setattr(suite, 'write_json', cancel)
    assert suite.run(args, content, plan, shards) == 130
    assert json.loads((root / 'orchestration.json').read_text())['status'] == 'interrupted'
    assert not (root / 'comparison').exists()
