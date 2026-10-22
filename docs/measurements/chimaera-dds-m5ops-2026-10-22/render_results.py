#!/usr/bin/env python3
"""Create a standalone interactive view of the checked short-run summary."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
report = json.loads((root / 'timing-short-results.json').read_text())
streams = ('left_image', 'right_image', 'joint_states', 'effort_command', 'clock')
baseline = [dict(label=row['label'], implementation=row['implementation'],
                 metrics={s: row['metrics'][s]['latency'] for s in streams})
            for row in report['baseline'] if row.get('metrics')]
new = next(row for row in report['baseline'] if row['implementation'] == 'direct_dds_m5ops')
bulk = next(row for row in report['bulk'] if row['implementation'] == 'direct_dds_m5ops')
controls = []
for stream, label in (('joint_states', 'Joints H→G'),
                      ('effort_command', 'Effort G→H'), ('clock', 'Clock H→G')):
    a, b = new['metrics'][stream], bulk['metrics'][stream]
    controls.append(dict(label=label, baseline=a['latency'], loaded=b['latency'],
                         max_age_ms=b['age']['max_ms'],
                         missed_releases=b['missed_source_releases']))
data = dict(baseline=baseline, controls=controls)
page = '''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Short timing CPU comparison</title><style>
body{font-family:var(--font-sans,system-ui);color:var(--foreground,#222);margin:0;line-height:1.5}
p{margin:0 0 12px}.muted{color:var(--muted-foreground,#666);font-size:13px}
strong{font-weight:650}.controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:16px 0 8px;font-size:14px}
select{font:inherit;color:inherit;background:var(--background,white);border:1px solid var(--border,#aaa);border-radius:6px;padding:5px 9px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:8px 7px;border-bottom:1px solid var(--border,#ddd);white-space:nowrap}
th:first-child,td:first-child{text-align:left;padding-left:0}th{font-weight:550;color:var(--muted-foreground,#666)}
tr.new td{font-weight:650;background:var(--accent-surface,rgba(70,130,180,.09))}
.panel{padding:16px;border-radius:var(--radius,8px);background:var(--warning-surface,rgba(200,140,40,.09));margin:18px 0}
.panel p:last-child{margin-bottom:0}.section{font-size:15px;font-weight:650;margin:0 0 10px}
.fine{font-size:13px}.panel table{margin:10px 0 12px}.foot{margin-top:16px}
</style></head><body>
<p><strong>All eight ball-like baselines completed. Eleven of twelve cases completed overall.</strong><br>
The new backend improved baseline latency versus scheduling DDS/DMA. Large-payload control isolation remains incomplete.</p>
<p class="muted">Two TimingSimpleCPUs at 3 GHz · 0.2 s source window · 0.05 s warmup · 0.1 s drain.<br>
Stereo 480×360 RGB 30 Hz · joints/efforts 250 Hz · clock 1 kHz. Two shared native-free references; no stepped runs.</p>
<div class="controls"><label for="stat">Baseline publication-to-callback latency:</label><select id="stat"><option value="p50_ms">Median</option><option value="max_ms">Observed maximum</option><option value="mean_ms">Mean</option></select><span>milliseconds</span></div>
<div class="scroll"><table><thead><tr><th>Implementation</th><th>Image L</th><th>Image R</th><th>Joints H→G</th><th>Effort G→H</th><th>Clock H→G</th></tr></thead><tbody id="baseline"></tbody></table></div>
<p class="muted foot">Every baseline delivered its 6+6 measured stereo frames, 50 joints, 50 efforts and 200 clocks through drain. Stereo cohort delivery was 31.104 MB/s normalized to source duration. This does not establish maximum capacity; receipt-window rates can include delayed warmup frames. Guest clock windows each missed one scheduled release; integrated DMA also missed one scheduled effort release.</p>
<div class="panel"><p class="section">8 MiB traffic still delays control</p>
<p class="fine">The new backend delivered both measured 8 MiB samples: H→G latency 119.00 ms and G→H 81.73 ms. They arrived during drain. Small-control latency increased despite separate transport lanes.</p>
<div class="scroll"><table><thead><tr><th>New backend</th><th>Ball median / max</th><th>Bulk median / max</th><th>Bulk max age</th></tr></thead><tbody id="controls"></tbody></table></div>
<p class="fine">Effort publication-to-callback latency hides source delay: maximum release lag was 78.47 ms, and 12 scheduled effort releases were absent from the source window. Measured control age reached 80 ms for efforts and 127.41 ms for clocks.</p>
</div>
<p class="fine"><strong>Scheduling DDS/DMA’s bulk case failed with “controller queue limit exceeded.”</strong> Its historical controller has 32 MiB directional queue caps. The new backend has larger, separate lane budgets. That outcome cannot yield a numeric DMA-to-m5ops bulk speedup.</p>
<p class="muted">One short window, about 6 frames/eye and 50 joint/effort samples: medians and maxima are descriptive, with no confidence-based tail or sustainable-throughput claim. m5ops uses functional/coherent payload copies instead of timed DMA requests; this changes the memory-cost model. READY binary pins are established; exact retained-source correspondence is unverified.</p>
<script>const data=__DATA__;
const streams=['left_image','right_image','joint_states','effort_command','clock'];
const n=x=>x===null?'—':x.toFixed(2);
const pair=x=>n(x.p50_ms)+' / '+n(x.max_ms);
function draw(){const stat=document.getElementById('stat').value;document.getElementById('baseline').innerHTML=data.baseline.map(r=>'<tr class="'+(r.implementation==='direct_dds_m5ops'?'new':'')+'"><td>'+r.label+'</td>'+streams.map(s=>'<td>'+n(r.metrics[s][stat])+'</td>').join('')+'</tr>').join('');}
document.getElementById('controls').innerHTML=data.controls.map(r=>'<tr><td>'+r.label+'</td><td>'+pair(r.baseline)+'</td><td>'+pair(r.loaded)+'</td><td>'+n(r.max_age_ms)+'ms</td></tr>').join('');
document.getElementById('stat').addEventListener('change',draw);draw();
console.log('Validated summary:',data.baseline.length,'baseline rows;',data.controls.length,'control contrasts');
</script></body></html>'''.replace('__DATA__', json.dumps(data))
(root/'timing-short-results.html').write_text(page)
print(json.dumps(dict(html=page, baseline_rows=len(baseline), control_rows=len(controls))))
