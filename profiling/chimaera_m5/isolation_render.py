#!/usr/bin/env python3
"""Self-contained interactive view of the bounded isolation experiments."""
import argparse
import json
from pathlib import Path

from common import read_json, sha256, write_json
from isolation_report import report


PAGE = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{margin:0;color:var(--foreground,#20252c);font-family:var(--font-sans,system-ui);font-size:14px;line-height:1.5}h2{font-size:22px;line-height:1.2;margin:0 0 8px}h3{font-size:16px;margin:22px 0 8px}p{margin:8px 0}.muted,small{color:var(--muted-foreground,#586270)}.controls{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:18px 0}label{display:grid;gap:4px;font-size:12px}select{color:inherit;background:var(--card,#f4f6f8);border:1px solid var(--border,#ccd2d9);border-radius:6px;padding:7px;font:inherit}.chartrow,.axis{display:grid;grid-template-columns:minmax(95px,150px) minmax(100px,1fr) 105px;gap:12px;align-items:center}.chartrow{min-height:45px;border-bottom:1px solid var(--border,#ddd)}.rowlabel{font-size:12px}.track{height:24px;position:relative;background:linear-gradient(90deg,var(--border,#ddd) 1px,transparent 1px);background-size:25% 100%;border-right:1px solid var(--border,#ddd)}.dot{position:absolute;top:8px;width:9px;height:9px;border-radius:50%;transform:translateX(-50%);background:var(--chart-1,#1768ac)}.loaded .dot{background:var(--chart-2,#bd6331);border-radius:1px;transform:translateX(-50%) rotate(45deg)}.span{position:absolute;height:3px;top:11px;background:var(--chart-1,#1768ac);opacity:.35}.number{font:12px var(--font-mono,monospace);text-align:right}.axis{font-size:11px;color:var(--muted-foreground,#586270);margin:8px 0 18px}.ticks{display:flex;justify-content:space-between}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12px}td,th{text-align:right;padding:8px 6px;border-bottom:1px solid var(--border,#ddd);vertical-align:top}td:first-child,th:first-child{text-align:left}th{font-weight:600}.good{color:var(--success,#28713d)}.bad{color:var(--destructive,#b63333)}details{margin-top:16px}summary{cursor:pointer}code{font:12px var(--font-mono,monospace)}.note{padding:16px;border-radius:var(--radius,8px);background:var(--muted,#f0f2f5)}@media(max-width:450px){.chartrow,.axis{gap:7px;grid-template-columns:95px minmax(85px,1fr) 86px}.number{font-size:11px}h2{font-size:20px}}
</style></head><body><h2>Stream latency under competing image traffic</h2>
<p class="muted">Two guest cores · simulation time · fixed robot-shaped stereo/control traffic</p>
<p id="status"></p><p id="budget" class="muted"></p><div class="controls"><label>Experiment<select id="experiment"></select></label><label>Stream<select id="stream"><option value="joint_states">Joint feedback · 250 Hz</option><option value="effort_command">Effort command · 250 Hz</option><option value="clock">Clock · 1 kHz</option><option value="left_image">Left camera · 30 Hz</option><option value="right_image">Right camera · 30 Hz</option><option value="bulk_image_h2g">8 MiB · host → guest</option><option value="bulk_image_g2h">8 MiB · guest → host</option></select></label><label>Latency boundary<select id="metric"><option value="creation_to_callback">Logical creation → callback</option><option value="latency">Publish entry → callback</option><option value="age">Scheduled release → callback</option></select></label><label><span>Axis</span><select id="scale"><option value="linear">Linear</option><option value="log">Log (1 + ms)</option></select></label></div>
<p><strong id="metricTitle"></strong></p><div id="chart" role="img" aria-label="Stream age by layout and load"></div>
<div class="axis"><span></span><div class="ticks" id="ticks"></div><span></span></div>
<p class="muted">Circles: unloaded windows. Diamonds: bidirectional 8 MiB overlap. Range lines connect observed windows; they are not confidence intervals.</p>
<div class="note"><strong>Interpretation</strong><p>Each timing window has a 0.2 s source period: about 50 joint/effort and 200 clock receipts. Joint/effort p99 therefore equals the observed maximum. Repetitions describe variability; these tests do not establish sustainable throughput or a tail guarantee.</p><p>Creation-start latency begins before logical per-sample preparation of a reused payload. Initial allocation and physical sensor capture are excluded. Publish-entry latency starts at publication; scheduled-release age also includes source scheduling delay. These are distinct boundaries. Host computation while gem5 is paused has zero simulation-time cost in both modes, and host timestamps use the coupling clock.</p><p>The callback marker follows a lifecycle guard. Trace recording and locking add guest work; observer overhead has not been isolated.</p><p id="boundary"></p></div>
<h3>Source coverage and delivery</h3><p class="muted">Triples below are joints / effort / clock. Missing source releases are separate from transmitted samples missing at drain.</p><div class="scroll"><table><thead><tr><th>Case</th><th>Received</th><th>Missing releases</th><th>Missing at drain</th><th>Publish max, guest bulk</th></tr></thead><tbody id="coverage"></tbody></table></div>
<h3>Actual robot application</h3><p id="robot"></p>
<details><summary>Scope, correctness and resource evidence</summary><div id="evidence"></div></details>
<script>const data=__DATA__;
const experiments=document.querySelector('#experiment'),stream=document.querySelector('#stream'),scale=document.querySelector('#scale'),metric=document.querySelector('#metric');
const names=['joint_states','effort_command','clock'];
const fmt=n=>n==null?'—':Number(n).toFixed(2);
function element(tag,text,cls){let e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;}
for(const [index,item] of data.experiments.entries()){let o=element('option',item.title);o.value=index;experiments.append(o);}
function draw(){document.querySelector('#metricTitle').textContent=(metric.value==='age'?'Scheduled release → callback marker':metric.value==='creation_to_callback'?'Logical creation start → callback marker':'Publish call entry → callback marker')+', p99 (ms)';const item=data.experiments[Number(experiments.value)];if(!item)return;const valid=item.result.attempts.filter(a=>a.status==='complete'&&!a.errors.length&&a.metrics);const groups=new Map();for(const a of valid){const c=a.case,mode=a.config.middleware_copy_mode||'guest',key=[c.execution_layout,mode,c.cell].join('/');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(a);}
let rows=[...groups].map(([key,attempts])=>({key,attempts,values:attempts.map(a=>a.metrics[stream.value]?.[metric.value]?.p99_ms).filter(n=>n!=null)})).filter(r=>r.values.length);let max=Math.max(1,...rows.flatMap(r=>r.values))*1.08;
const position=v=>100*(scale.value==='log'?Math.log1p(v)/Math.log1p(max):v/max);document.querySelector('#chart').replaceChildren();for(const r of rows){const loaded=r.attempts[0].case.cell!=='ball',row=element('div',null,'chartrow'+(loaded?' loaded':'')),parts=r.key.split('/'),label=(parts[0]==='split'?'Split':'Merged')+' · '+(parts[1]==='functional'?'functional copies':'guest copies')+' · '+(loaded?'8 MiB':'unloaded');row.append(element('div',label+' ('+r.values.length+')','rowlabel'));const track=element('div',null,'track'),lo=Math.min(...r.values),hi=Math.max(...r.values);if(r.values.length>1){const span=element('span',null,'span');span.style.left=position(lo)+'%';span.style.width=(position(hi)-position(lo))+'%';track.append(span);}for(const value of r.values){const dot=element('span',null,'dot');dot.style.left=position(value)+'%';dot.title=fmt(value)+' ms';track.append(dot);}row.append(track,element('div',fmt(lo)+(hi!==lo?'–'+fmt(hi):'')+' ms','number'));document.querySelector('#chart').append(row);}
const ticks=document.querySelector('#ticks');ticks.replaceChildren();for(let i=0;i<5;i++){let v=scale.value==='log'?Math.expm1(Math.log1p(max)*i/4):max*i/4;ticks.append(element('span',fmt(v)));}
const all=item.result.attempts;document.querySelector('#status').textContent=valid.length+' / '+all.length+' validated timing cases. '+item.result.acceptance;document.querySelector('#boundary').textContent=item.kind==='copy'?'Functional-copy mode moves large, scoped middleware memory copies/fills onto the host. Application computation, prefault stores and the remaining middleware stay in the guest. Both arms preload the same helper; this is a separate memory-cost model.':'Both layouts retain one DDS participant and the same receive pools. Split execution adds one application executor thread; guest serialization and DDS memory work remain modeled.';
const table=document.querySelector('#coverage');table.replaceChildren();for(const a of all){let tr=element('tr');tr.append(element('td',a.case.name));if(!a.metrics||a.errors.length){let td=element('td',a.status+': '+a.errors.join('; '),'bad');td.colSpan=4;tr.append(td);}else{for(const field of ['received','missed_source_releases','missing_at_drain'])tr.append(element('td',names.map(n=>a.metrics[n][field]).join(' / ')));let bulk=a.metrics.bulk_image_g2h;tr.append(element('td',bulk?fmt(bulk.publish_call.max_ms)+' ms':'—'));}table.append(tr);}
const ev=document.querySelector('#evidence');ev.replaceChildren();ev.append(element('p','KVM correctness: '+data.kvmStatus+'. Correctness windows scan full image bodies; main timing windows disable that scan. The existing clock schedule may miss one release even unloaded.'));ev.append(element('p','Timing counters retain their original dump scope, which can include warmup, drain and export. Overlapping dumps are not summed. Internal per-flow queue/credit occupancy and process RSS are not measured by this diagnostic.'));for(const a of valid){const dumps=a.guest_statistics?.completed_dumps||[],last=dumps[dumps.length-1];if(last)ev.append(element('p',a.case.name+': last raw simInsts = '+(last.simInsts??'unavailable').toLocaleString()));}if(item.result.copy_boundary_evidence?.length){for(const r of item.result.copy_boundary_evidence)ev.append(element('p',r.case+': functional operation evidence '+(r.valid?'valid':'INVALID')+', memory faults '+(r.memory_faults_observed?'observed':'none')));}console.log('Rendered',valid.length,'validated cases for',stream.value);}
experiments.addEventListener('change',draw);stream.addEventListener('change',draw);scale.addEventListener('change',draw);metric.addEventListener('change',draw);
document.querySelector('#robot').textContent=data.robotText;document.querySelector('#budget').textContent=data.budgetText;metric.value=data.defaultMetric;draw();
</script></body></html>'''


def render(root, output, copy_subdir='copy-modes'):
    experiments = []
    for folder, title, kind in ((root / 'timing-r1', 'Application execution isolation', 'execution'),
                                (root / copy_subdir / 'timing-r1', 'Middleware memory-cost boundary', 'copy')):
        if (folder / 'suite.json').is_file():
            result = report(folder)
            experiments.append(dict(title=title, kind=kind, result=result))
    kvm_status = []
    for name, folder in (('execution', root / 'kvm-r1'), ('copy boundary', root / copy_subdir / 'kvm-r1')):
        if (folder / 'suite.json').is_file():
            attempts = report(folder)['attempts']
            count = sum(a['status'] == 'complete' and not a['errors'] for a in attempts)
            kvm_status.append(f'{name} {count} / {len(attempts)} validated')
    robot_directories = sorted(path for path in root.glob('actual-robot-kvm-r*') if path.is_dir())
    latest_robot = robot_directories[-1] if robot_directories else None
    scored = [path for path in robot_directories if (path / 'baseline/result.json').is_file()]
    scored_robot = scored[-1] if scored else latest_robot
    robot = read_json(scored_robot / 'baseline/result.json') if scored_robot else None
    if robot:
        robot_text = ('Catch succeeded' if robot.get('success', robot.get('caught')) else 'Catch did not succeed')
        robot_text += ': ' + str(robot.get('reason')) + '. Actual application uses the fixed two-core geometry; this KVM check is separate from timing measurements.'
        gate = read_json(root / 'two-core-catch-pass.json')
        if gate and gate.get('execution_layout') == 'composed_candidate':
            robot_text += ' This pass uses the opt-in composed candidate: one process/DDS context and two executors. The retained three-process layout did not reach readiness.'
        copy_mode = read_json(scored_robot / 'copy-mode.json')
        if copy_mode:
            robot_text += ' Scored copy mode: ' + str(copy_mode.get('mode')) + '.'
        if latest_robot != scored_robot:
            robot_text += ' A later attempt has no scored result; the earlier score is retained here.'
    else:
        log = latest_robot / 'baseline/launch.log' if latest_robot else None
        failed = log and log.is_file() and 'guest finished before its bridge was ready' in log.read_text(errors='replace')
        robot_text = ('Actual two-core robot run failed before bridge readiness (m5_fail code 2); no catch was scored. '
                      if failed else 'Actual two-core robot validation has no scored result. ')
        robot_text += ('Scene generation and kinematic validation pass in the complete application image. '
                       'The smaller SDK attempt lacked robot dependencies. The historical successful '
                       'four-core catch does not qualify this two-core configuration.')
    budget = read_json(root / 'round-budget.json')
    budget_text = ''
    if budget:
        budget_text = f"Host wall-time cap: {budget['cap_seconds']} seconds."
        if budget.get('elapsed_seconds') is not None:
            budget_text += f" Round stopped after {budget['elapsed_seconds']:.0f} seconds."
        if budget.get('final_case_status') == 'budget_exhausted':
            budget_text += ' Final reference stopped at the budget cutoff; no complete result.'
        if budget.get('no_owned_simulation_containers_running'):
            budget_text += ' No owned simulations remain running.'
    creation_recorded = any(
        row.get('metrics', {}).get('joint_states', {}).get('creation_to_callback', {}).get('count', 0)
        for experiment in experiments for row in experiment['result']['attempts'] if row.get('metrics'))
    data = dict(experiments=experiments, kvmStatus='; '.join(kvm_status), robotText=robot_text,
                budgetText=budget_text, defaultMetric='creation_to_callback' if creation_recorded else 'latency')
    visible = dict(data, experiments=[])
    for experiment in experiments:
        result = experiment['result']
        attempts = []
        for row in result['attempts']:
            metrics = row.get('metrics')
            if metrics:
                metrics = {name: {key: value for key, value in metric.items() if key in (
                    'age', 'latency', 'creation_to_callback', 'received', 'missed_source_releases', 'missing_at_drain', 'publish_call')}
                    for name, metric in metrics.items() if name in (
                        'joint_states', 'effort_command', 'clock', 'left_image', 'right_image',
                        'bulk_image_h2g', 'bulk_image_g2h')}
            dumps = row.get('guest_statistics', {}).get('completed_dumps', [])
            attempts.append(dict(case=row['case'], config=dict(
                middleware_copy_mode=row.get('config', {}).get('middleware_copy_mode', 'guest')),
                status=row['status'], errors=row['errors'], metrics=metrics,
                guest_statistics=dict(completed_dumps=[{'simInsts': dumps[-1].get('simInsts')}]
                                      if dumps else [])))
        view = dict(attempts=attempts, acceptance=result['acceptance'],
            copy_boundary_evidence=[{key: value for key, value in item.items() if key != 'dumps'}
                                    for item in result.get('copy_boundary_evidence', [])])
        visible['experiments'].append(dict(experiment, result=view))
    serialized = json.dumps(visible, allow_nan=False).replace('<', '\\u003c')
    output.write_text(PAGE.replace('__DATA__', serialized))
    write_json(output.with_suffix('.json'), dict(data=visible, renderer_sha256=sha256(Path(__file__))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    render(args.root, args.output)


if __name__ == '__main__':
    main()
