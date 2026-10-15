import json
from types import SimpleNamespace

from reports import build_report


def test_comparison_uses_embedded_models_without_ros_or_original_meshes(tmp_path):
    cases = []
    for index in range(2):
        case = f'case-{index:03d}'
        path = tmp_path / 'results/e/attempts' / f'a{index}' / 'suite/dashboard.html'
        path.parent.mkdir(parents=True)
        data = dict(models={f'model{index}': {'links': []}}, trials=[{'id': case}],
                    points=[dict(id=case, status='complete', trials=[case])])
        path.write_text('<html><script>window.EXPERIMENT_DATA = ' + json.dumps(data) + ';</script><p>Frozen viewer</p></html>')
        cases.append(dict(id=case, point=dict(id=case), status='completed',
            attempts=[dict(id=f'a{index}', status='completed', collected=True)]))
    cases.append(dict(id='case-002', point=dict(id='case-002'), status='queued', attempts=[]))
    cases.append(dict(id='case-003', point=dict(id='case-003'), status='failed', attempts=[]))
    report = build_report(SimpleNamespace(data=tmp_path), dict(id='e', cases=cases)).read_text()
    data = json.JSONDecoder().raw_decode(report.split('window.EXPERIMENT_DATA = ')[1])[0]
    assert len(data['models']) == 2
    assert len(data['trials']) == 2
    assert len(data['points']) == 4
    assert data['points'][2]['status'] == 'pending'
    assert data['points'][3]['status'] == 'infrastructure_failure'
    assert 'Frozen viewer' in report
