#!/usr/bin/env python3
"""Render a standalone, interactive DDS image pipeline report."""
import argparse
import json
from pathlib import Path


def render(data):
    compact = dict(data)
    compact['cases'] = []
    for case in data['cases']:
        worst = [max(samples, key=lambda s: s['total_ns']) for camera in (1, 2)
                 if (samples := [s for s in case['samples'] if s['stream'] == camera])]
        compact['cases'].append(dict(
            case=case['case'], mode=case['mode'], errors=case['errors'],
            dma_join_errors=case['dma_join_errors'],
            camera={key: {name: value[name] for name in ('total', 'stages')}
                    for key, value in case['camera'].items()},
            samples=[{key: sample[key] for key in
                      ('stream', 'capture_ns', 'total_ns', 'stages_ns')} for sample in worst]))
    return TEMPLATE.replace('__DATA__', json.dumps(compact).replace('</', '<\\/'))


TEMPLATE = '''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DDS image pipeline profile</title>
<style>
:root{color-scheme:light dark;--local-muted:light-dark(#626975,#a6b1bf);--local-border:light-dark(#d6dce3,#344052);--local-card:light-dark(#f4f6f8,#17202c)}
body{margin:0;font:14px/1.5 var(--font-sans,system-ui);color:var(--foreground,CanvasText)}
*{box-sizing:border-box}p{margin:10px 0}strong{font-weight:650}.muted,small{color:var(--muted-foreground,var(--local-muted))}
.controls{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}label{display:grid;gap:5px;font-size:12px;flex:1;min-width:130px}
select{width:100%;padding:8px;border:1px solid var(--border,var(--local-border));border-radius:6px;background:var(--background,Canvas);color:inherit;font:inherit}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:14px 0}.card{padding:16px;background:var(--card,var(--local-card));border-radius:var(--radius,8px)}
.number{font-size:25px;font-weight:650;line-height:1.3}.caption{font-size:12px;color:var(--muted-foreground,var(--local-muted))}
.bars{margin:20px 0}.barrow{display:grid;grid-template-columns:minmax(140px,1fr) 2fr 65px;align-items:center;gap:10px;margin:11px 0;font-size:12px}
.track{height:18px;background:var(--card,var(--local-card));border-radius:3px;overflow:hidden}.fill{height:18px;min-width:1px;background:var(--chart-1,#428bce);border-radius:3px}.value{font-variant-numeric:tabular-nums;text-align:right}
details{margin:18px 0}summary{cursor:pointer;font-weight:600}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;margin:12px 0;font-size:12px}td,th{border-bottom:1px solid var(--border,var(--local-border));padding:8px 5px;text-align:left}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.note{padding:16px;background:var(--card,var(--local-card));border-radius:var(--radius,8px);font-size:12px;margin:14px 0}
@media(max-width:430px){.cards{gap:6px}.card{padding:16px}.number{font-size:20px}.barrow{grid-template-columns:125px 1fr 58px;gap:6px}}
</style>
<body>
<p><strong>DDS image pipeline · KVM profiling</strong><br><span class="muted">480 × 360 RGB stereo at 30 Hz · reliable delivery · DMA transport</span></p>
<p>The remaining delay is spread across guest interrupt handling, IPC forwarding and ROS executor service. The earlier <strong>20.70 ms</strong> mean was measured with TimingSimpleCPU; the new runs below all use KVM or native controls.</p>
<div class="controls"><label>Profile case<select id="case"></select></label><label>Camera<select id="camera"><option value="2">Right image</option><option value="1">Left image</option></select></label><label>Attribution<select id="sample"><option value="mean">Mean of joined images</option><option value="worst">Slowest joined image</option></select></label></div>
<div class="cards"><div class="card"><div class="number" id="total"></div><div class="caption">Publish → callback</div></div><div class="card"><div class="number" id="afterdma"></div><div class="caption" id="afterlabel">After DMA completion</div></div><div class="card"><div class="number" id="count"></div><div class="caption">Images attributed</div></div></div>
<div id="bars" class="bars" aria-label="Pipeline delay by stage"></div>
<p class="muted" id="sample-note"></p>
<div class="note"><strong>Read these as diagnostic measurements.</strong> Trace calls change KVM execution and scheduling. Native simulation timestamps have 1 ms buckets. The default KVM event-queue quantum is 1 ms, which limits cross-core interval precision; invalid causal joins are rejected. DMA payload duration is zero in this KVM configuration, so these runs cannot establish its detailed memory-system cost.</div>
<details><summary>Every pipeline stage</summary><div class="scroll"><table><thead><tr><th>Interval</th><th class="num">Mean / selected ms</th><th class="num">P95 ms</th></tr></thead><tbody id="stages"></tbody></table></div></details>
<details open><summary>Matched controls and ablations</summary><div class="scroll"><table><thead><tr><th>Case</th><th class="num">Left mean</th><th class="num">Right mean</th><th class="num">Right max</th></tr></thead><tbody id="matrix"></tbody></table></div></details>
<p class="muted"><small>Each case has a 0.25 s warmup, 0.5 s source window and 0.5 s drain: 15 measured images per camera. <span id="delivery-note"></span> All times are milliseconds. Short-run maxima are observations, not stable tail estimates. The full report records startup failures, overlapping pilot activity, trace overhead and exact artifact hashes.</small></p>
<script>
const data=__DATA__;
const $=id=>document.getElementById(id);
const names={publish_to_serialize:'Publish → serializer',serialization:'CDR serialization',dds_send_and_packet_allocation:'DDS send / packet allocation',host_ipc_and_router_parse:'Host IPC / parsing',host_router_boundary_wait:'Host router wait',host_controller_and_mailbox:'Controller / mailbox copy',admission_to_dma_header:'Mailbox → DMA header',dma_descriptor_setup:'DMA descriptor setup',dma_payload:'DMA payload',irq_driver_and_completion_thread:'IRQ / completion thread',guest_bridge_scheduling:'Guest bridge scheduling',guest_decode_and_bridge_exchange:'DMA decode / bridge exchange',guest_router_and_ipc:'Guest router / IPC',dds_reader_history_and_executor_wait:'DDS receive / executor service',deserialization:'Typed deserialization',rclcpp_to_callback:'rclcpp → callback',local_dds_and_executor:'Local DDS / executor'};
const groups=[['Host publication / routing',['publish_to_serialize','serialization','dds_send_and_packet_allocation','host_ipc_and_router_parse','host_router_boundary_wait','host_controller_and_mailbox']],['Admission / DMA',['admission_to_dma_header','dma_descriptor_setup','dma_payload']],['IRQ / bridge scheduling',['irq_driver_and_completion_thread','guest_bridge_scheduling']],['Guest forwarding / IPC',['guest_decode_and_bridge_exchange','guest_router_and_ipc']],['DDS / executor service',['dds_reader_history_and_executor_wait','local_dds_and_executor']],['Deserialize / callback',['deserialization','rclcpp_to_callback']]];
const fmt=n=>n.toFixed(3);
data.cases.forEach(c=>{if(c.samples.length){const o=document.createElement('option');o.value=c.case;o.textContent=c.case;$('case').append(o)}});
if(data.cases.some(c=>c.case==='kvm-trace-verify0'))$('case').value='kvm-trace-verify0';
function update(){
 const c=data.cases.find(c=>c.case===$('case').value),cam=$('camera').value;
 const ss=c.samples.filter(s=>String(s.stream)===cam),summary=c.camera[cam];
 if(!ss.length)return;
 const worst=ss.reduce((a,b)=>a.total_ns>b.total_ns?a:b),isWorst=$('sample').value==='worst';
 const stages=isWorst?Object.fromEntries(Object.entries(worst.stages_ns).map(([k,v])=>[k,v/1e6])):Object.fromEntries(Object.entries(summary.stages).map(([k,v])=>[k,v.mean_ms]));
 const total=isWorst?worst.total_ns/1e6:summary.total.mean_ms;
 $('total').textContent=fmt(total)+' ms';$('count').textContent=isWorst?'1 / '+summary.total.count:String(summary.total.count);
 const after=['irq_driver_and_completion_thread','guest_bridge_scheduling','guest_decode_and_bridge_exchange','guest_router_and_ipc','dds_reader_history_and_executor_wait','deserialization','rclcpp_to_callback'].reduce((n,k)=>n+(stages[k]||0),0);
 $('afterdma').textContent=fmt(c.mode==='chimaera'?after:stages.local_dds_and_executor)+' ms';$('afterlabel').textContent=c.mode==='chimaera'?'After DMA completion':'Local DDS / executor';
 const values=groups.map(([name,keys])=>[name,keys.reduce((n,k)=>n+(stages[k]||0),0)]),max=Math.max(...values.map(v=>v[1]),.001);
 $('bars').replaceChildren();values.forEach(([name,v],i)=>{const row=document.createElement('div');row.className='barrow';row.innerHTML='<span>'+name+'</span><div class="track"><div class="fill" style="width:'+(100*v/max)+'%;background:var(--chart-'+(i+1)+',#428bce)"></div></div><span class="value">'+fmt(v)+'</span>';$('bars').append(row)});
 $('stages').innerHTML=Object.entries(stages).map(([k,v])=>'<tr><td>'+names[k]+'</td><td class="num">'+fmt(v)+'</td><td class="num">'+(isWorst?'—':fmt(summary.stages[k].p95_ms))+'</td></tr>').join('');
 $('sample-note').textContent=(isWorst?'Capture '+(worst.capture_ns/1e6).toFixed(3)+' ms. ':'')+c.errors.length+' image joins rejected; '+c.dma_join_errors.length+' DMA join errors. Stage means sum to the attributed callback delay.';
}
for(const id of ['case','camera','sample'])$(id).addEventListener('change',update);
$('matrix').innerHTML=(data.matrix||[]).map(c=>'<tr><td>'+c.name+(c.clock_missing_at_drain?' †':'')+'</td><td class="num">'+fmt(c.left_mean_ms)+'</td><td class="num">'+fmt(c.right_mean_ms)+'</td><td class="num">'+fmt(c.right_max_ms)+'</td></tr>').join('');
if(data.matrix?.length){$('delivery-note').textContent=data.matrix.reduce((n,c)=>n+c.images_received,0)+' measured images delivered. '+data.matrix.filter(c=>c.clock_missing_at_drain).map(c=>'† '+c.name+': '+c.clock_missing_at_drain+' of '+c.clock_published+' clock messages censored at drain; image delivery is complete, whole-workload delivery is incomplete.').join(' ')}
update();console.log('pipeline report ready',data.cases.length,'profile cases');
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('analysis', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(render(json.loads(args.analysis.read_text())))


if __name__ == '__main__':
    main()
