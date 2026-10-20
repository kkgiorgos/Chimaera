#!/usr/bin/env python3
"""Render the matched integrated acceptance export as a standalone HTML artifact."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('attempts', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    record = json.loads(args.attempts.read_text())
    if not record.get('comparisons_validated'):
        parser.error('Render a matched acceptance export, not diagnostic history')
    def metric(value):
        if value is None:
            return None
        return {key: value.get(key) for key in
                ('published', 'received', 'latency', 'age', 'duplicates', 'reordered')}
    data = []
    for attempt in record['attempts']:
        if attempt['result'] != 'complete' or not attempt['summary']:
            continue
        summary = attempt['summary']
        config = summary['config']
        data.append(dict(case=attempt['case'], backend=config.get('transport_backend', 'm5'),
                         mode=config['mode'], policy=config.get('delivery_policy', 'latest'),
                         workload=config['workload'], duration=config['duration'],
                         metrics={key: metric(value) for key, value in
                                  (summary.get('streams') or summary['directions']).items()},
                         pair=metric(summary.get('stereo_pairs'))))
    html = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Integrated Chimaera DMA acceptance</title>
<style>
:root{color-scheme:light dark}*{box-sizing:border-box}body{margin:0;font:14px/1.5 var(--font-sans,system-ui);color:var(--foreground,CanvasText)}h2{font-size:22px;margin:0}p{margin:7px 0 16px}.muted{color:var(--muted-foreground,GrayText)}fieldset{display:flex;gap:8px;flex-wrap:wrap;border:0;padding:0;margin:16px 0}legend{position:absolute;width:1px;height:1px;overflow:hidden}label,select{padding:6px 9px;border:1px solid var(--border,GrayText);border-radius:6px;background:var(--card,Canvas);color:inherit}input{accent-color:#6688df}.group{margin:22px 0}.name{font-weight:650;margin-bottom:8px}.row{display:grid;grid-template-columns:120px 1fr 145px;gap:8px;align-items:center;margin:6px 0;font-size:12px}.track{height:14px;background:var(--card,Canvas);border-radius:3px}.bar{height:100%;min-width:1px;background:var(--color);border-radius:3px}.value{text-align:right;font-variant-numeric:tabular-nums}.count{color:var(--muted-foreground,GrayText)}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}th,td{text-align:right;padding:7px;border-bottom:1px solid var(--border,GrayText);font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}.note{padding:14px;background:var(--card,Canvas);border-radius:8px;margin-top:18px}.note p:last-child{margin-bottom:0}@media(max-width:480px){.row{grid-template-columns:90px 1fr 112px;font-size:11px}h2{font-size:19px}}
.loss{color:var(--warning,#c98b30);font-weight:650}.native{display:block;font-size:11px;color:var(--muted-foreground,GrayText)}
</style><body>
<h2>DMA integrated into the ROS path</h2>
<p class="muted">ubuntu-george · 2 × TimingSimpleCPU · 3 GHz · 2 ms synchronization</p>
<p>Same ROS endpoints and 16 MiB DDS profile for m5, DMA and their same-machine native controls. Stereo: 640×480 RGB, 30 Hz, with 250 Hz joints/control and 1 kHz clock traffic.</p>
<fieldset><legend>Workload</legend>
<label><input name="shape" type="radio" value="control" checked> 64-byte controls</label>
<label><input name="shape" type="radio" value="latest"> Stereo · latest</label>
<label><input name="shape" type="radio" value="lossless"> Stereo · lossless</label></fieldset>
<fieldset><legend>Comparison</legend>
<select id="metric" aria-label="Latency statistic"><option value="mean">Mean callback delay</option><option value="p99">p99 callback delay</option><option value="age">Mean age from scheduled release</option><option value="age99">p99 age from scheduled release</option></select>
<select id="native" aria-label="Native execution mode"><option value="native_stepped">Stepped native</option><option value="native_free">Free-running native</option></select></fieldset>
<div id="chart" aria-live="polite"></div>
<p class="muted">Bars show simulation milliseconds and received / published counts. Callback delay starts at actual publication; age starts at scheduled release. A complete stereo pair ends at the later camera callback.</p>
<div class="scroll"><table><thead><tr><th>Stream / backend</th><th>Received</th><th>Mean / native ms</th><th>p99 ms</th><th>Age max ms</th><th>Duplicates / reordered</th></tr></thead><tbody id="table"></tbody></table></div>
<div class="note"><strong>Interpretation</strong><p>Each measurement window is 2 s: 194 publications per control direction or 60 frames per camera. This is a sustained-window check, with limited evidence for rare tails. The stereo latest case preserves application sensor queues and permits replacement. Stereo lossless uses reliable depth 128 for all five measured streams and disables replacement and expiry.</p>
<p>Native timestamps have 1 ms clock buckets. The gap to native includes modeled guest CPU and memory costs. Full-byte image validation and DMA guard/reset tests are separate from these performance runs. The DMA model includes coherent memory timing and IRQ handling; a separate PCI link-rate limit is not modeled.</p></div>
<script>const runs=DATA;
const names={host_to_guest:'Host → guest',guest_to_host:'Guest → host',left_image:'Left camera',right_image:'Right camera',pair:'Complete stereo pair',joint_states:'Joint states',effort_command:'Effort command',clock:'Clock'};
function metric(m,key){return key==='age'||key==='age99'?m.age?.[key==='age'?'mean_ms':'p99_ms']:m.latency?.[key==='mean'?'mean_ms':'p99_ms']}
function number(v){return v==null?'n/a':v.toFixed(2)}
function render(){const shape=document.querySelector('input[name=shape]:checked').value,native=document.querySelector('#native').value,key=document.querySelector('#metric').value;
const selected=runs.filter(r=>r.duration===2&&(shape==='control'?r.workload==='synthetic':r.workload==='ball_catching'&&r.policy===shape));
const streams=shape==='control'?['host_to_guest','guest_to_host']:['left_image','right_image','pair'];let html='';
for(const stream of streams){let values=[];for(const backend of ['m5','dma']){for(const mode of ['chimaera',native]){const r=selected.find(r=>r.backend===backend&&r.mode===mode);const m=stream==='pair'?r?.pair:r?.metrics[stream];if(m)values.push({label:backend.toUpperCase()+(mode==='chimaera'?'':' native'),value:metric(m,key),m,color:mode!=='chimaera'?'#3da48d':backend==='dma'?'#6688df':'#a39170'});}}
const max=Math.max(...values.map(v=>v.value??0),1)*1.08;html+='<div class="group"><div class="name">'+names[stream]+'</div>';
for(const v of values){html+='<div class="row"><span>'+v.label+'</span><div class="track"><div class="bar" style="--color:'+v.color+';width:'+((v.value??0)/max*100)+'%"></div></div><span class="value">'+number(v.value)+' ms <span class="count">· '+v.m.received+'/'+v.m.published+'</span></span></div>';}html+='</div>';}
document.querySelector('#chart').innerHTML=html;let table='';for(const r of selected.filter(r=>r.mode==='chimaera'))for(const [stream,m] of Object.entries(r.metrics)){const n=selected.find(candidate=>candidate.backend===r.backend&&candidate.mode===native)?.metrics[stream],missing=m.received<m.published;table+='<tr><td>'+names[stream]+' / '+r.backend.toUpperCase()+'</td><td><span class="'+(missing?'loss':'')+'">'+m.received+'/'+m.published+(missing?' ('+(100*m.received/m.published).toFixed(1)+'%)':'')+'</span><span class="native">native '+(n?n.received+'/'+n.published:'n/a')+'</span></td><td>'+number(m.latency.mean_ms)+' / '+number(n?.latency.mean_ms)+'</td><td>'+number(m.latency.p99_ms)+'</td><td>'+number(m.age?.max_ms)+'</td><td>'+m.duplicates+' / '+m.reordered+'</td></tr>';}
document.querySelector('#table').innerHTML=table;}
document.querySelectorAll('input,select').forEach(e=>e.addEventListener('change',render));render();console.log('Matched integrated cases:',runs.length);
</script></body></html>'''
    args.output.write_text(html.replace('const runs=DATA;', 'const runs=' + json.dumps(data).replace('<', '\\u003c') + ';'))


if __name__ == '__main__':
    main()
