"""Merge frozen worker reports, retaining their embedded robot meshes.

URDFs reference image-local ROS installations. Combining rendered report data
avoids requiring ROS or rewriting raw trial records on the webserver.
"""
import json
import uuid


def build_report(controller, experiment):
    base = controller.data / 'results' / experiment['id']
    models, trials, points, template = {}, [], [], None
    for case in experiment['cases']:
        eligible = [a for a in case['attempts'] if a.get('collected') and a['status'] == 'completed']
        status = {'failed': 'infrastructure_failure', 'stopped': 'interrupted',
                  'running': 'running', 'dispatching': 'running', 'stopping': 'running',
                  'collecting': 'running'}.get(case['status'], 'pending')
        point = dict(case['point'], status=status, trials=[])
        if eligible:
            path = base / 'attempts' / eligible[-1]['id'] / 'suite/dashboard.html'
            page = path.read_text()
            marker = 'window.EXPERIMENT_DATA = '
            start = page.index(marker) + len(marker)
            data, length = json.JSONDecoder().raw_decode(page[start:])
            if template is None:
                template = (page[:start], page[start + length:])
            models.update(data['models'])
            trials.extend(data['trials'])
            point = next(p for p in data['points'] if p['id'] == case['id'])
        points.append(point)
    if template is None:
        raise ValueError('No completed cases are available for a report')
    payload = json.dumps(dict(schema_version=2, models=models, trials=trials, points=points),
                         separators=(',', ':'), allow_nan=False).replace('</', r'<\/')
    destination = base / 'comparison/dashboard.html'
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_name(uuid.uuid4().hex + '.tmp')
    temporary.write_text(template[0] + payload + template[1])
    temporary.replace(destination)
    return destination
