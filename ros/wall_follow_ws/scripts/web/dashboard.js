'use strict';
const data = JSON.parse(document.getElementById('dataset').textContent);
const $ = id => document.getElementById(id);
const colors = ['#007d87','#db7130','#6e55b5','#c33a62','#298651','#456ccb','#946e25','#a24b95'];
data.runs.forEach((r,i) => r.color = i<colors.length ? colors[i] : `hsl(${i*137.5%360} 60% 38%)`);
const metrics = [
 ['rmse_m','Tracking RMSE (m)'],['mae_m','Tracking MAE (m)'],['max_abs_error_m','Max absolute error (m)'],
 ['gt_coverage','Ground-truth coverage'],['path_m','Distance travelled (m)'],['stopped_fraction','Stopped fraction'],
 ['stale_fraction','Stale-scan fraction'],['compute_p95_ms','Computation p95 (ms)'],['compute_max_ms','Computation max (ms)'],
 ['actual_control_hz_sim','Actual control rate (Hz, sim)'],['timer_interval_p95_wall_ms','Timer interval p95 (ms, wall)'],
 ['scan_age_p95_s','Scan age p95 (s, sim)'],['real_time_factor','Real-time factor'],
 ['controller_cpu_core_fraction','Controller CPU (core fraction)'],['rss_peak_mib','Peak controller RSS (MiB)'],
 ['sim_duration_s','Recorded duration (s)']];
const signals = [['gt_error','Wall-distance error (m)'],['compute_ms','Control computation (ms)'],
 ['scan_age','Scan age (s, sim)'],['dt_wall','Timer interval (s, wall)'],['path_m','Cumulative distance (m)']];
const hash = new URLSearchParams(location.hash.slice(1));
let selected = new Set(hash.has('runs') ? hash.get('runs').split(',') : data.runs.slice(0,3).map(r=>r.id));
selected = new Set([...selected].filter(id=>data.runs.some(r=>r.id===id)));
function elem(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
function finite(v) { return typeof v==='number' && Number.isFinite(v); }
function fmt(v) { if(v===undefined||v===null)return 'Not recorded'; if(typeof v==='number')return finite(v)?Number(v.toPrecision(5)).toString():'Unavailable'; if(typeof v==='object')return JSON.stringify(v); return String(v); }
function active() { return data.runs.filter(r=>selected.has(r.id)); }
function dot(r) { const e=elem('span',undefined,'dot'); e.style.background=r.color; return e; }
function setOptions(id, options) { for(const [value,label] of options) { const o=elem('option',label);o.value=value;$(id).append(o); } }
setOptions('bar-metric',metrics); setOptions('signal',signals);
$('total').textContent=`(${data.runs.length})`;
$('context').textContent=`Summary warmup: ${data.warmup} simulation seconds · Built ${new Date(data.generated).toLocaleString()}`;
$('footer').textContent='Each configuration combines independent repetitions with equal weight per run. Scalar metrics are computed from all original samples in each run before averaging; p95 means the mean of per-run p95s, not a pooled percentile. Bands/error bars show sample standard deviation, not confidence intervals; n=1 has no SD estimate. No outliers are removed. Signal plots interpolate within the common recorded time interval onto a bounded grid; pointwise n may fall when data is missing. Mean trajectories are time-aligned averages, not actual robot paths. Time filters affect charts only. CPU/RSS cover the controller process. Scan age uses simulation time. Missing data stays unavailable. Regenerate this offline snapshot to add results or change warmup.';
if(data.errors.length) { $('errors').hidden=false; $('errors').textContent='Some runs could not be loaded:\n'+data.errors.join('\n'); }
function visible() { const query=$('search').value.toLowerCase();return data.runs.filter(r=>JSON.stringify([r.name,r.path,r.config]).toLowerCase().includes(query)); }
function renderLibrary() {
 $('run-list').replaceChildren();
 for(const r of visible()) {
  const label=elem('label',undefined,'run'+(selected.has(r.id)?' selected':''));label.title=r.path;
  const check=elem('input');check.type='checkbox';check.checked=selected.has(r.id);check.setAttribute('aria-label',`Select ${r.name}`);
  check.addEventListener('change',()=>{if(check.checked)selected.add(r.id);else selected.delete(r.id);render();});
  const text=elem('div');const name=elem('div',undefined,'name');name.append(dot(r),document.createTextNode(r.name));
  text.append(name,elem('small',`${r.config.architecture??'Architecture not recorded'} · ${r.completed?'Complete':'INCOMPLETE'}`),
   elem('small',`${r.arena.arena_width} × ${r.arena.arena_height} m · ${r.count} repetitions${r.events.length?' · LIVE CHANGES':''}`));
  label.append(check,text);$('run-list').append(label);
 }
 $('selection-count').textContent=`${selected.size} selected · ${visible().length} shown`;
}
function comparisonTable(container, runs, records, rows, highlight=false) {
 const table=elem('table'), head=elem('thead'), tr=elem('tr');tr.append(elem('th','Setting / metric'));
 for(const r of runs) { const th=elem('th');th.append(dot(r),document.createTextNode(r.name));th.title=r.path;tr.append(th); }head.append(tr);table.append(head);
 const body=elem('tbody');
 for(const [key,label] of rows) {
  const values=records.map(rec=>rec[key]);const different=new Set(values.map(v=>JSON.stringify(v)??'__missing__')).size>1;
  if(highlight&&$('differences-only').checked&&!different)continue;
  const row=elem('tr',undefined,highlight&&different?'different':'');row.append(elem('td',label));
  for(const value of values) { const cell=elem('td',fmt(value));if(value===undefined||value===null)cell.className='unknown';row.append(cell); }
  body.append(row);
 }
 table.append(body);container.replaceChildren(table);
 return body.children.length;
}
function renderConfig(runs) {
 const records=runs.map(r=>({...r.config,'runtime.parameter_events':r.events,...($('provenance').checked?Object.fromEntries(Object.entries(r.provenance).map(([k,v])=>['provenance.'+k,v])):{})}));
 const keys=[...new Set(records.flatMap(r=>Object.keys(r)))].sort();
 const count=comparisonTable($('config-table'),runs,records,keys.map(k=>[k,k]),true);
 $('config-note').textContent=!count?(runs.length<2?'Select two or more configurations to see differences, or turn off “Differences only”.':'No differences in these settings. Enable software / hardware details to inspect provenance, or disable “Differences only” to view shared values.'):`${count} ${$('differences-only').checked?'differing':'configuration'} fields. “Not recorded” means unknown, not a default.`;
 $('events').replaceChildren();
 for(const r of runs) {
  const details=elem('details');details.append(elem('summary',`${r.name}: ${r.count} included repetition(s)`));
  const list=elem('ul');for(const member of r.members)list.append(elem('li',member.path));details.append(list);
  details.append(elem('p',r.interval?`Signal overlap: ${fmt(r.interval[0])}–${fmt(r.interval[1])} simulation seconds.`:'No common time interval; aggregate scalar metrics are still available.','hint'));
  $('events').append(details);
 }
 for(const r of runs.filter(r=>r.events.length)) {
  const details=elem('details');details.append(elem('summary',`${r.name}: ${r.events.length} live parameter change(s) — initial settings alone do not describe this run`));
  details.append(elem('pre',JSON.stringify({parameter_events:r.events,final_parameters:r.final_parameters},null,2)));$('events').append(details);
 }
}
const NS='http://www.w3.org/2000/svg';
function svgElem(tag, attrs={}, text) { const e=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attrs))e.setAttribute(key,String(value));if(text!==undefined)e.textContent=text;return e; }
function chart(container, label, w=720,h=320) { const svg=svgElem('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':label});svg.append(svgElem('title',{},label));container.replaceChildren(svg);return svg; }
function domain(values, includeZero=false) { let lo=includeZero?0:Infinity,hi=includeZero?0:-Infinity;for(const v of values)if(finite(v)){lo=Math.min(lo,v);hi=Math.max(hi,v);}if(!Number.isFinite(lo))return [0,1];if(lo===hi){const pad=Math.max(.01,Math.abs(lo)*.05);lo-=pad;hi+=pad;}return [lo,hi]; }
function axes(svg,xd,yd,w=720,h=320) {
 const l=64,t=20,r=w-22,b=h-48;const x=v=>l+(v-xd[0])/(xd[1]-xd[0])*(r-l),y=v=>b-(v-yd[0])/(yd[1]-yd[0])*(b-t);
 for(let i=0;i<=4;i++){const xv=xd[0]+i*(xd[1]-xd[0])/4,yv=yd[0]+i*(yd[1]-yd[0])/4;
 svg.append(svgElem('line',{x1:l,x2:r,y1:y(yv),y2:y(yv),class:'grid'}),svgElem('text',{x:l-8,y:y(yv)+4,'text-anchor':'end'},Number(yv.toPrecision(3))),svgElem('text',{x:x(xv),y:b+22,'text-anchor':'middle'},Number(xv.toPrecision(3))));}
 return {x,y,l,t,r,b};
}
function attachHover(svg, points, w=720,h=320) {
 svg.addEventListener('pointermove',event=>{
  const rect=svg.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*w,py=(event.clientY-rect.top)/rect.height*h;
  let best=null,dist=900;for(const p of points){const d=(p.x-px)**2+(p.y-py)**2;if(d<dist){best=p;dist=d;}}
  if(!best){$('tooltip').hidden=true;return;}
  $('tooltip').textContent=best.text;$('tooltip').hidden=false;
  $('tooltip').style.left=Math.max(8,Math.min(event.clientX+12,window.innerWidth-350))+'px';
  $('tooltip').style.top=Math.max(8,Math.min(event.clientY+12,window.innerHeight-120))+'px';
 });svg.addEventListener('pointerleave',()=>{$('tooltip').hidden=true;});
}
function renderBars(runs) {
 const key=$('bar-metric').value,label=metrics.find(m=>m[0]===key)[1],svg=chart($('bar-chart'),label,900,270);
 const yd=domain(runs.flatMap(r=>{const st=r.metric_stats[key];return [st.mean,finite(st.mean)?st.mean+(st.std??0):null,finite(st.mean)?st.mean-(st.std??0):null];}),true),left=66,right=880,top=15,bottom=220;
 const y=v=>bottom-(v-yd[0])/(yd[1]-yd[0])*(bottom-top),width=(right-left)/runs.length;
 for(let i=0;i<=4;i++){const val=yd[0]+i*(yd[1]-yd[0])/4;svg.append(svgElem('line',{x1:left,x2:right,y1:y(val),y2:y(val),class:'grid'}),svgElem('text',{x:left-8,y:y(val)+4,'text-anchor':'end'},Number(val.toPrecision(3))));}
 runs.forEach((run,i)=>{const val=run.metrics[key],x=left+width*i+width*.18;
  if(finite(val)){const rect=svgElem('rect',{x,y:Math.min(y(val),y(0)),width:width*.64,height:Math.max(1,Math.abs(y(val)-y(0))),fill:run.color,rx:3});rect.append(svgElem('title',{},`${run.name}\n${label}: ${fmt(val)} ± ${fmt(run.metric_stats[key].std)}\nn=${run.metric_stats[key].n}`));svg.append(rect);
   const sd=run.metric_stats[key].std;if(finite(sd)){const cx=left+width*(i+.5);svg.append(svgElem('line',{x1:cx,x2:cx,y1:y(val-sd),y2:y(val+sd),stroke:'#172b40','stroke-width':2}),svgElem('line',{x1:cx-5,x2:cx+5,y1:y(val+sd),y2:y(val+sd),stroke:'#172b40'}),svgElem('line',{x1:cx-5,x2:cx+5,y1:y(val-sd),y2:y(val-sd),stroke:'#172b40'}));}}
  svg.append(svgElem('text',{x:left+width*(i+.5),y:finite(val)?Math.max(12,y(val+(run.metric_stats[key].std??0))-6):top+16,'text-anchor':'middle'},finite(val)?fmt(val):'Unavailable'),svgElem('text',{x:left+width*(i+.5),y:bottom+23,'text-anchor':'middle'},`Group ${i+1} (n=${run.metric_stats[key].n})`));
 });
 comparisonTable($('metrics-table'),runs,runs.map(r=>({completed:r.completed,repetitions:r.count,...Object.fromEntries(metrics.map(([k])=>{
  const st=r.metric_stats[k];return [k,st.n?`${fmt(st.mean)} ± ${st.std===null?'SD unavailable':fmt(st.std)} (n=${st.n})`:'Unavailable (n=0)'];
 }))})),[['completed','Runs completed'],['repetitions','Repetitions'],...metrics]);
}
function timeRange(runs) {
 const a=Number($('from').value),b=$('to').value===''?Math.max(1,...runs.map(r=>r.series.elapsed.at(-1)).filter(finite)):Number($('to').value);
 const valid=$('from').value!==''&&finite(a)&&finite(b)&&a>=0&&b>a;
 $('time-error').hidden=valid;return valid?[a,b]:null;
}
function renderTraces(runs) {
 const range=timeRange(runs);if(!range){$('signal-chart').replaceChildren();$('trajectory-chart').replaceChildren();return;}
 const key=$('signal').value,label=signals.find(s=>s[0]===key)[1];
 const datasets=runs.map(run=>({run,points:run.series.elapsed.map((time,i)=>({time,x:run.series.x[i],y:run.series.y[i],v:run.series[key][i],sd:run.series_std[key][i],n:run.series_n[key][i]})).filter(p=>p.time>=range[0]&&p.time<=range[1])}));
 const signalSvg=chart($('signal-chart'),label);const sa=axes(signalSvg,range,domain(datasets.flatMap(d=>d.points.flatMap(p=>[p.v,finite(p.v)?p.v+(p.sd??0):null,finite(p.v)?p.v-(p.sd??0):null]))));
 signalSvg.append(svgElem('text',{x:392,y:316,'text-anchor':'middle'},'Elapsed simulation time (s)'));
 const hover=[];
 for(const {run,points} of datasets){
  let band=[];const flushBand=()=>{if(band.length){signalSvg.append(svgElem('polygon',{points:band.map(p=>`${sa.x(p.time)},${sa.y(p.v+p.sd)}`).concat([...band].reverse().map(p=>`${sa.x(p.time)},${sa.y(p.v-p.sd)}`)).join(' '),fill:run.color,opacity:.15}));}band=[];};
  for(const p of points){if(finite(p.v)&&finite(p.sd))band.push(p);else flushBand();}flushBand();
  let segment=[];const flush=()=>{if(segment.length)signalSvg.append(svgElem('polyline',{points:segment.join(' '),fill:'none',stroke:run.color,'stroke-width':1.6}));segment=[];};
  for(const p of points){if(!finite(p.v)){flush();continue;}const x=sa.x(p.time),y=sa.y(p.v);segment.push(`${x},${y}`);hover.push({x,y,text:`${run.name}\nTime: ${fmt(p.time)} s\n${label}: ${fmt(p.v)} ± ${p.sd===null?'SD unavailable':fmt(p.sd)}\nn=${p.n}`});}flush();}
 attachHover(signalSvg,hover);
 const trajectory=chart($('trajectory-chart'),'World trajectory and arena walls');
 let xd=domain(datasets.flatMap(d=>[...d.points.map(p=>p.x),-d.run.arena.arena_width/2,d.run.arena.arena_width/2]));
 let yd=domain(datasets.flatMap(d=>[...d.points.map(p=>p.y),-d.run.arena.arena_height/2,d.run.arena.arena_height/2]));
 // Preserve physical aspect ratio: a metre has the same scale on both axes.
 const scale=Math.max((xd[1]-xd[0])/634,(yd[1]-yd[0])/252)*1.08;
 const xc=(xd[0]+xd[1])/2,yc=(yd[0]+yd[1])/2;xd=[xc-scale*634/2,xc+scale*634/2];yd=[yc-scale*252/2,yc+scale*252/2];
 const ta=axes(trajectory,xd,yd),th=[];
 for(const {run,points} of datasets){const hx=run.arena.arena_width/2,hy=run.arena.arena_height/2;
  const outline=svgElem('rect',{x:ta.x(-hx),y:ta.y(hy),width:ta.x(hx)-ta.x(-hx),height:ta.y(-hy)-ta.y(hy),fill:'none',stroke:run.color,'stroke-dasharray':'5 4',opacity:.4});outline.append(svgElem('title',{},`${run.name}: ${hx*2} × ${hy*2} m arena`));trajectory.append(outline);
  let segment=[];const flush=()=>{if(segment.length)trajectory.append(svgElem('polyline',{points:segment.join(' '),fill:'none',stroke:run.color,'stroke-width':2}));segment=[];};
  for(const p of points){if(!finite(p.x)||!finite(p.y)){flush();continue;}const x=ta.x(p.x),y=ta.y(p.y);segment.push(`${x},${y}`);th.push({x,y,text:`${run.name}\nTime: ${fmt(p.time)} s\nx: ${fmt(p.x)} m · y: ${fmt(p.y)} m`});}flush();}
 attachHover(trajectory,th);
 $('legend').replaceChildren();runs.forEach((r,i)=>{const item=elem('span');item.append(dot(r),document.createTextNode(`Group ${i+1}: ${r.name} (n=${r.count})`));$('legend').append(item);});
}
function render() {
 const runs=active();renderLibrary();$('headline').textContent=`${runs.length} configuration${runs.length===1?'':'s'} · ${runs.reduce((n,r)=>n+r.count,0)} repetitions`;
 $('empty').hidden=!!runs.length;$('content').hidden=!runs.length;$('export').disabled=!runs.length;
 try{history.replaceState(null,'','#runs='+runs.map(r=>r.id).join(','));}catch(_){}
 $('tooltip').hidden=true;if(!runs.length)return;renderConfig(runs);renderBars(runs);renderTraces(runs);
}
$('search').addEventListener('input',renderLibrary);
$('select-visible').addEventListener('click',()=>{visible().forEach(r=>selected.add(r.id));render();});
$('clear').addEventListener('click',()=>{selected.clear();render();});
for(const id of ['differences-only','provenance'])$(id).addEventListener('change',()=>renderConfig(active()));
$('bar-metric').addEventListener('change',()=>renderBars(active()));
for(const id of ['signal','from','to'])$(id).addEventListener('change',()=>renderTraces(active()));
$('reset-time').addEventListener('click',()=>{$('from').value=0;$('to').value='';renderTraces(active());});
$('export').addEventListener('click',()=>{
 const rows=active().map(r=>({group_id:r.id,configuration:r.name,repetitions:r.count,members:r.path,completed:r.completed,
  ...Object.fromEntries(Object.entries(r.metric_stats).flatMap(([k,stats])=>Object.entries(stats).map(([stat,v])=>[k+'_'+stat,v]))),
  ...r.config,...r.provenance,parameter_events:JSON.stringify(r.events)}));
 const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];
 const quote=v=>{let s=v==null?'':String(v);if(typeof v==='string'&&/^\s*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
 const csv=[keys.map(quote).join(','),...rows.map(r=>keys.map(k=>quote(r[k])).join(','))].join('\r\n');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=elem('a');a.href=url;a.download='selected_groups.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
render();
