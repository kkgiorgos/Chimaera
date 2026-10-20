#!/usr/bin/env python3
"""Create a self-contained interactive view of the retained matched pilot CSV."""
import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rows = [row for row in csv.DictReader(args.csv.open())
            if row['mode'] == 'chimaera' and row['cpu'] == 'timing'
            and row['variant'] in ('baseline', 'candidate-v2')]
    data = json.dumps(rows).replace('<', '\\u003c')
    html = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chimaera rewrite pilot</title><style>
:root{color-scheme:light dark}
*{box-sizing:border-box}body{margin:0;color:var(--foreground,CanvasText);font:14px/1.5 var(--font-sans,system-ui,sans-serif)}h2{font-size:21px;margin:0 0 4px;font-weight:650}p{margin:6px 0 14px}.muted{color:var(--muted-foreground,GrayText)}
fieldset{border:0;padding:0;margin:12px 0;display:flex;flex-wrap:wrap;gap:8px}legend{position:absolute;width:1px;height:1px;overflow:hidden}label{padding:6px 10px;border:1px solid var(--border,GrayText);border-radius:8px;cursor:pointer}label:has(input:checked){background:var(--card,Canvas)}input{accent-color:#6387d9}
.legend{display:flex;gap:15px;flex-wrap:wrap;font-size:12px;margin:14px 0}.key:before{content:'';width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:5px;background:var(--series)}
.group{margin:18px 0 25px}.name{font-weight:600;margin-bottom:9px}.row{display:grid;grid-template-columns:83px 1fr 136px;gap:10px;align-items:center;margin:5px 0;font-size:12px}.track{height:14px;background:var(--card,Canvas);border-radius:3px}.bar{height:100%;min-width:2px;background:var(--series);border-radius:3px}.value{font-variant-numeric:tabular-nums;text-align:right}.receipt{color:var(--muted-foreground,GrayText);font-size:11px}.note{padding:16px;border-radius:var(--radius,12px);background:var(--card,Canvas);margin-top:18px}.note p:last-child{margin-bottom:0}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:7px 0;text-align:left;border-bottom:1px solid var(--border,GrayText);font-variant-numeric:tabular-nums}th:not(:first-child),td:not(:first-child){text-align:right}small{font-size:12px}@media(max-width:450px){.row{grid-template-columns:65px 1fr 110px;gap:6px}.receipt{font-size:10px}h2{font-size:19px}}
</style><body>
<h2>First measured rewrite</h2><p class="muted">TimingSimpleCPU · 2 cores · 3 GHz · ubuntu-george</p>
<p>Streamed payloads, fewer image copies, and callback-driven guest exchanges.
The objective remains the smallest gap to native execution.</p>
<fieldset><legend>Traffic</legend><label><input type="radio" name="traffic" value="control" checked> 64-byte messages</label><label><input type="radio" name="traffic" value="images"> Stereo images</label></fieldset>
<fieldset><legend>Native clock mode</legend><label><input type="radio" name="native" value="stepped" checked> Stepped native</label><label><input type="radio" name="native" value="free"> Free-running native</label></fieldset>
<div class="legend"><span class="key" style="--series:#94a0b4">Deployed</span><span class="key" style="--series:#6387d9">Rewrite</span><span class="key" style="--series:#40a78d">Matched native</span></div>
<div id="chart" aria-live="polite"></div>
<p class="muted"><small>Mean publication → receiving-callback delay, in simulation ms. Each native bar is matched to its corresponding implementation. Native logical clocks advance in 1 ms steps: a mean of zero means the same clock bucket.</small></p>
<div class="note"><strong>Promising pilot, incomplete acceptance evidence</strong><p>Only 0.2 s of source traffic: about 20 small messages or six images per camera. Stereo is 640×480 RGB at 30 Hz. These runs do not establish sustained throughput or stable latency tails.</p>
<p>Latest-value policies remain active. In the mixed image workload, the rewrite received 46/50 joint updates, 17/51 control commands and 16/200 clock updates. Lower delay among surviving messages alone cannot establish success.</p></div>
<p style="margin-top:20px"><strong>Experimental DMA device · separate measurement</strong></p>
<table><thead><tr><th>Payload</th><th>Device round trip</th><th>Native copies</th></tr></thead><tbody>
<tr><td>64 bytes</td><td>8.18 µs</td><td>0.026 µs</td></tr>
<tr><td>Stereo 640×480 RGB</td><td>0.381 ms</td><td>0.259 ms</td></tr>
<tr><td>Stereo 1280×960 RGB</td><td>1.477 ms</td><td>1.023 ms</td></tr>
</tbody></table>
<p class="muted"><small>Two samples per size. Detailed CPU device loopback versus two native memory copies on the same machine. ROS callback work is outside this microbenchmark.</small></p>
<p>Eighteen byte/guard checks, invalid requests and reset/reopen passed. The real host-mailbox echo takes about 2 ms at 2 ms synchronization boundaries. ROS buffer-pool integration and sustained workload testing remain next.</p>
<script>const rows=DATA;
function render(){const mode=document.querySelector('input[name=traffic]:checked').value;
const native=document.querySelector('input[name=native]:checked').value;
const streams=mode==='images'?[['left_image','Left camera'],['right_image','Right camera']]:[['host_to_guest','Host → guest'],['guest_to_host','Guest → host']];
let html='';for(const [stream,label] of streams){const old=rows.find(r=>r.variant==='baseline'&&r.stream===stream),now=rows.find(r=>r.variant==='candidate-v2'&&r.stream===stream);
const key='native_'+native;const values=[['Deployed',+old.mean_ms,old.received+'/'+old.published,'#94a0b4'],['Its native',+old[key+'_mean_ms'],'100%','#40a78d'],['Rewrite',+now.mean_ms,now.received+'/'+now.published,'#6387d9'],['Its native',+now[key+'_mean_ms'],'100%','#40a78d']];
const scale=Math.max(...values.map(v=>v[1]),1)*1.08;html+='<div class="group"><div class="name">'+label+'</div>';
for(const [name,value,count,color] of values){html+='<div class="row"><span>'+name+'</span><div class="track"><div class="bar" style="--series:'+color+';width:'+value/scale*100+'%"></div></div><span class="value">'+value.toFixed(2)+' ms <span class="receipt">· '+count+'</span></span></div>';}html+='</div>';}
document.querySelector('#chart').innerHTML=html;}
document.querySelectorAll('input').forEach(i=>i.addEventListener('change',render));render();
console.log('Pilot comparison ready:',rows.length,'matched stream rows');</script></body></html>'''
    args.output.write_text(html.replace('const rows=DATA;', 'const rows=' + data + ';'))


if __name__ == '__main__':
    main()
