#!/usr/bin/env python3
"""Render the retained KVM copy comparison as a standalone interactive page."""
import argparse
import json
from pathlib import Path


PAGE = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DDS copy reduction · KVM comparison</title>
<style>
*{box-sizing:border-box}body{margin:0;color:var(--foreground,#243041);font:14px/1.5 var(--font-sans,system-ui)}
p{margin:12px 0}small,.muted{color:var(--muted-foreground,#657184)}
.stats{display:flex;gap:20px;flex-wrap:wrap;margin:8px 0 22px}.stats div{flex:1;min-width:140px}
.stats strong{font-size:30px;font-weight:650;display:block;letter-spacing:-1px}
label{display:inline-flex;flex-direction:column;gap:5px;margin:0 12px 16px 0}
select{font:inherit;border:1px solid var(--border,#cbd2db);color:inherit;background:var(--background,#fff);padding:7px 10px;border-radius:6px}
.legend{display:flex;gap:18px;margin:10px 0}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.baseline{background:var(--chart-1,#825cd3)}.candidate{background:var(--chart-2,#269ba7)}
.chart{min-height:190px;margin:14px 0}.run{display:grid;grid-template-columns:100px 1fr 70px;align-items:center;gap:10px;margin:9px 0}
.track{height:17px;background:var(--muted,#eef0f4);border-radius:3px;overflow:hidden}.bar{height:17px;border-radius:3px}
.value{font-variant-numeric:tabular-nums;text-align:right}table{border-collapse:collapse;width:100%;margin:14px 0}td,th{padding:8px 5px;text-align:right;border-bottom:1px solid var(--border,#d9dee5);font-variant-numeric:tabular-nums}td:first-child,th:first-child{text-align:left}
.copy{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:20px 0}.copy span{padding:6px 9px;background:var(--muted,#eef0f4);border-radius:5px}.copy .kept{background:var(--accent-surface,#dff1f1)}
details{margin:18px 0}summary{cursor:pointer}.note{border-left:3px solid var(--border,#cbd2db);padding-left:12px}
@media(max-width:460px){.stats{gap:10px}.stats strong{font-size:25px}.run{grid-template-columns:88px 1fr 60px;gap:5px}td,th{font-size:12px}}
</style>
<div class="stats"><div><strong>8 → 1</strong>counted host → guest transport copies</div>
<div><strong id="change"></strong>pair mean · verification off</div><div><strong id="images"></strong>images in matched comparisons</div></div>
<p>Owning packets replace copy chains. Scatter/gather writes avoid joining the DDS header and payload; DMA loans stay reserved through the last recipient.</p>
<div class="copy"><span>DDS payload</span>→<span>socket → owning packet</span>→<span class="kept">mailbox copy</span>→<span>DMA loan → socket</span>→<span>DDS reader</span></div>
<label>Stream<select id="stream"><option value="stereo_pairs">Complete stereo pair</option value="left_image">Left image</option value="right_image">Right image</option value="joint_states">Joint feedback</option value="effort_command">Effort command</option value="clock">Clock</option></select></label>
<label>Run cohort<select id="cohort"><option value="performance">Verification off · 3 repeats</option><option value="verified">Every image byte verified · 3 repeats</option></select></label>
<label>Per-run metric<select id="metric"><option value="mean_ms">Mean delay</option><option value="p95_ms">Observed p95</option><option value="max_ms">Observed maximum</option></select></label>
<div class="legend"><span><i class="dot baseline"></i>Baseline</span><span><i class="dot candidate"></i>Shared ownership</span></div>
<div id="chart" class="chart" role="img" aria-label="Per-run latency comparison in simulation milliseconds"></div>
<table><thead><tr><th>Implementation</th><th>Received / published</th><th>Mean delay</th><th>Maximum</th></tr></thead><tbody id="table"></tbody></table>
<p class="muted" id="details"></p>
<p class="note" id="verified-result"></p>
<p class="note"><b>Scope:</b> fresh two-core KVM guests, 2 ms coupling, 480 × 360 RGB stereo at 30 Hz, 250 Hz joint/effort traffic, 1 kHz clock. Each source window lasts 1 s, after 0.25 s warmup, with 0.75 s drain. Both versions use the same endpoints, RMW, simulator, kernel and DDS profile.</p>
<details><summary>Delivery counts for all streams</summary><table><thead><tr><th>Case</th><th>Stream</th><th>Received / published</th><th>Missing / duplicate / reordered</th></tr></thead><tbody id="counts"></tbody></table></details>
<p class="muted">Copy counts cover full-payload userspace copies in the custom transport/coordinator on the host → guest path. Kernel socket copies, DDS history and typed serialization/deserialization remain. Guest → host also retains TX staging and the host mailbox read. KVM does not model detailed CPU/memory costs. Short independent runs do not establish stable tail latency or isolate memcpy cost.</p>
<script>
const report=__DATA__;
const performance=report.aggregate.find(x=>x.cohort==='performance'&&x.stream==='stereo_pairs');
const delta=performance.change_percent;
document.querySelector('#change').textContent=(delta<0?'−':'+')+Math.abs(delta).toFixed(1)+'%';
const images=report.rows.filter(x=>['left_image','right_image'].includes(x.stream));
document.querySelector('#images').textContent=images.reduce((n,x)=>n+x.received,0)+' / '+images.reduce((n,x)=>n+x.published,0);
const number=x=>x==null?'—':x.toFixed(2)+' ms';
const verified=report.aggregate.find(x=>x.cohort==='verified'&&x.stream==='stereo_pairs');
document.querySelector('#verified-result').textContent=`With image-byte verification, pair mean ${verified.change_percent<0?'falls':'rises'} ${Math.abs(verified.change_percent).toFixed(1)}%: ${number(verified.baseline.mean_ms)} → ${number(verified.candidate.mean_ms)}. Copying is reduced in both cohorts; KVM latency does not improve uniformly. Verification runs after callback entry and can delay later callbacks.`;
function draw(){
 const stream=document.querySelector('#stream').value,cohort=document.querySelector('#cohort').value,metric=document.querySelector('#metric').value;
 const rows=report.rows.filter(x=>x.stream===stream&&x.cohort===cohort),max=Math.max(...rows.map(x=>x[metric]))||1;
 document.querySelector('#chart').innerHTML=rows.map(x=>`<div class="run"><small>${x.case}</small><div class="track"><div class="bar ${x.variant}" style="width:${x[metric]/max*100}%"></div></div><span class="value">${number(x[metric])}</span></div>`).join('');
 const aggregate=report.aggregate.find(x=>x.stream===stream&&x.cohort===cohort);
 document.querySelector('#table').innerHTML=['baseline','candidate'].map(v=>{const x=aggregate[v];return `<tr><td>${v==='baseline'?'Baseline':'Shared ownership'}</td><td>${x.received} / ${x.published}</td><td>${number(x.mean_ms)}</td><td>${number(x.max_ms)}</td></tr>`}).join('');
 document.querySelector('#details').textContent=`Mean across all received samples; maximum across the selected runs. Missing: ${rows.reduce((n,x)=>n+x.missing,0)}. Duplicates: ${rows.reduce((n,x)=>n+x.duplicates,0)}. Reordered: ${rows.reduce((n,x)=>n+x.reordered,0)}. Bars retain each independent run; p95 values are not averaged.`;
}
document.querySelectorAll('select').forEach(x=>x.addEventListener('change',draw));draw();
document.querySelector('#counts').innerHTML=report.rows.map(x=>`<tr><td>${x.case}</td><td>${x.stream}</td><td>${x.received} / ${x.published}</td><td>${x.missing} / ${x.duplicates} / ${x.reordered}</td></tr>`).join('');
console.log('KVM copy comparison:',report.attempts.length,'attempts;',report.all_delivered?'complete delivery':'delivery limitations');
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('comparison', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = json.loads(args.comparison.read_text())
    # Keep raw attempt/config provenance in JSON; the page needs only these fields.
    data = {key: report[key] for key in ('aggregate', 'rows', 'all_delivered')}
    data['attempts'] = [dict(name=item['name'], result=item['result']) for item in report['attempts']]
    args.output.write_text(PAGE.replace('__DATA__', json.dumps(data).replace('</', '<\\/')))


if __name__ == '__main__':
    main()
