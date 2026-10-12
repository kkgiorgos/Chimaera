'use strict';
const data = JSON.parse(document.getElementById('dataset').textContent);
const $ = id => document.getElementById(id);
const colors = ['#007d87','#db7130','#6e55b5','#c33a62','#298651','#456ccb','#946e25','#a24b95'];
data.runs.forEach((r,i) => r.color = i<colors.length ? colors[i] : `hsl(${i*137.5%360} 60% 38%)`);
const metrics = data.task_metrics;
const architectureMetrics = data.architecture_metrics;
const timingMetrics = data.simulation_metrics;
const memoryMetrics = data.memory_metrics ?? [];
setOptions('memory-metric', memoryMetrics);
const hasMemory = data.runs.some(r=>r.config['hardware.memory_backend']==='ramulator2');
$('memory-section').hidden=!hasMemory;
if(hasMemory) {
 $('memory-metric').value='gem5_cpu_dram_read_ns';
 document.title='Wall follower · Memory contention';
 document.querySelector?.('header h1') && (document.querySelector('header h1').textContent='Memory contention, task impact');
 document.querySelector?.('header p') && (document.querySelector('header p').textContent='Fixed hardware. Varying shared-memory workloads. Measured robot behavior.');
}
const signals = [['gt_error','Wall-distance error (m)'],['path_m','Cumulative distance (m)']];
const hash = new URLSearchParams(location.hash.slice(1));
let selected = new Set(hash.has('runs') ? hash.get('runs').split(',') : data.runs.map(r=>r.id));
selected = new Set([...selected].filter(id=>data.runs.some(r=>r.id===id)));
function elem(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
function finite(v) { return typeof v==='number' && Number.isFinite(v); }
function fmt(v, digits=5) {
 if(v===undefined||v===null)return 'Not recorded';
 if(typeof v==='number') {
  if(!finite(v))return 'Unavailable';
  if(v!==0&&(Math.abs(v)>=1e5||Math.abs(v)<1e-3))return v.toExponential(digits-1).replace(/\.?0+e/,'e');
  return Number(v.toPrecision(digits)).toString();
 }
 if(typeof v==='object')return JSON.stringify(v);
 return String(v);
}
function active() { return data.runs.filter(r=>selected.has(r.id)); }
function dot(r) { const e=elem('span',undefined,'dot'); e.style.background=r.color; return e; }
function setOptions(id, options) { for(const [value,label] of options) { const o=elem('option',label);o.value=value;$(id).append(o); } }
setOptions('timing-metric',timingMetrics);
if(!data.runs.some(r=>finite(r.metrics.timing_cosim_realtime_factor)))$('timing-metric').value='real_time_factor';
setOptions('architecture-metric',architectureMetrics);
$('timing-scope').textContent=data.timing_scope;
$('memory-metric').addEventListener('change',render);
setOptions('bar-metric',metrics); $('bar-metric').value='rmse_m'; setOptions('signal',signals);
$('total').textContent=`(${data.runs.length})`;
$('context').textContent=`Summary warmup: ${data.warmup} simulation seconds · Built ${new Date(data.generated).toLocaleString()}`;
$('footer').textContent='Repetitions have equal weight. Error bars and bands show sample standard deviation; n=1 has no SD estimate. Scalar metrics use all recorded samples after warmup. Traces interpolate within the common recorded time interval; mean trajectories are averages, not individual paths. Time filters affect charts only. Missing measurements remain unavailable.';
if(data.errors.length) { $('errors').hidden=false; $('errors').textContent='Comparison warnings:\n'+data.errors.join('\n'); }
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
function domain(values, includeZero=false) { let lo=includeZero?0:Infinity,hi=includeZero?0:-Infinity;for(const v of values)if(finite(v)){lo=Math.min(lo,v);hi=Math.max(hi,v);}if(!Number.isFinite(lo))return [0,1];if(lo===hi){const pad=Math.abs(lo)*.05||1;lo-=pad;hi+=pad;}return [lo,hi]; }
function axes(svg,xd,yd,w=720,h=320) {
 const l=64,t=20,r=w-22,b=h-48;const x=v=>l+(v-xd[0])/(xd[1]-xd[0])*(r-l),y=v=>b-(v-yd[0])/(yd[1]-yd[0])*(b-t);
 for(let i=0;i<=4;i++){const xv=xd[0]+i*(xd[1]-xd[0])/4,yv=yd[0]+i*(yd[1]-yd[0])/4;
 svg.append(svgElem('line',{x1:l,x2:r,y1:y(yv),y2:y(yv),class:'grid'}),svgElem('text',{x:l-8,y:y(yv)+4,'text-anchor':'end'},fmt(yv,3)),svgElem('text',{x:x(xv),y:b+22,'text-anchor':'middle'},fmt(xv,3)));}
 return {x,y,l,t,r,b};
}
function attachHover(svg, points) {
 svg.addEventListener('pointermove',event=>{
  const cursor=svg.createSVGPoint();cursor.x=event.clientX;cursor.y=event.clientY;
  const local=cursor.matrixTransform(svg.getScreenCTM().inverse()),px=local.x,py=local.y;
  let best=null,dist=900;for(const p of points){const d=(p.x-px)**2+(p.y-py)**2;if(d<dist){best=p;dist=d;}}
  if(!best){$('tooltip').hidden=true;return;}
  $('tooltip').textContent=best.text;$('tooltip').hidden=false;
  $('tooltip').style.left=Math.max(8,Math.min(event.clientX+12,window.innerWidth-350))+'px';
  $('tooltip').style.top=Math.max(8,Math.min(event.clientY+12,window.innerHeight-120))+'px';
 });svg.addEventListener('pointerleave',()=>{$('tooltip').hidden=true;});
}
// Sweep bundles stay one dimension; comparisons also expose varying fixed settings.
const dimensions = new Map();
for(const run of data.runs) for(const [name,bundle] of Object.entries(run.sweep??{})) {
 const keys=Object.keys(bundle).map(k=>Object.keys(run.config).find(p=>p===k||p.endsWith('.'+k))).filter(Boolean);
 if(keys.length) dimensions.set(name, keys);
}
const bundledKeys = new Set([...dimensions.values()].flat());
for(const key of [...new Set(data.runs.flatMap(r=>Object.keys(r.config)))].sort()) {
 if(!bundledKeys.has(key)&&!key.startsWith('source_sha256.')&&
    new Set(data.runs.map(r=>JSON.stringify(r.config[key]))).size>1) dimensions.set(key,[key]);
}
function settingLabel(key) { return key.replace(/^(hardware|controller|sensor|arena|cosimulation)\./,'').replaceAll('_',' '); }
function coordinate(run, name) {
 const keys=dimensions.get(name)??[];
 if(!keys.length||name==='workload')return run.name;
 return keys.length===1?run.config[keys[0]]:keys.map(k=>`${settingLabel(k)}=${fmt(run.config[k])}`).join(', ');
}
function axisLabel(name) {
 const keys=dimensions.get(name)??[];
 return keys.length===1?settingLabel(keys[0]):name==='configuration'?'Configuration settings':name;
}
setOptions('x-axis',[...dimensions.keys()].map(k=>[k,axisLabel(k)]));
setOptions('x-axis',[['configuration','Configuration settings']]);
const varyingAxis=[...dimensions.keys()].find(k=>new Set(data.runs.map(r=>JSON.stringify(coordinate(r,k)))).size>1);
if(varyingAxis)$('x-axis').value=varyingAxis;
function scalarTable(container,runs,options) {
 comparisonTable(container,runs,runs.map(r=>Object.fromEntries(options.map(([k])=>{
  const st=r.metric_stats[k];return [k,st?.n?`${fmt(st.mean)} ± ${st.std===null?'SD unavailable':fmt(st.std)} (n=${st.n})`:'Unavailable (n=0)'];
 }))),options);
}
function renderMetricBars(runs, key, label, container) {
 const xkey=$('x-axis').value;
 const entries=runs.map(run=>({run,value:run.metrics[key],st:run.metric_stats[key]??{n:0},label:coordinate(run,xkey)}));
 // Keep related settings adjacent without combining distinct configurations.
 entries.sort((a,b)=>String(a.label).localeCompare(String(b.label),undefined,{numeric:true}));
 // Widen the coordinate system with the bars so text and height stay stable.
 const plotWidth=Math.max(700,runs.length*130,container.clientWidth||0);
 const svg=chart(container,label,plotWidth,440),left=80,right=plotWidth-30,top=35,bottom=290;
 svg.style.width=plotWidth+'px';
 svg.style.height='440px';
 svg.append(svgElem('text',{x:12,y:18},label));
 const yd=domain(entries.flatMap(e=>[e.value,finite(e.value)?e.value+(e.st.std??0):null,finite(e.value)?e.value-(e.st.std??0):null]),true);
 const y=v=>bottom-(v-yd[0])/(yd[1]-yd[0])*(bottom-top),width=(right-left)/entries.length,hover=[];
 for(let i=0;i<=4;i++) {
  const v=yd[0]+i*(yd[1]-yd[0])/4;
  svg.append(svgElem('line',{x1:left,x2:right,y1:y(v),y2:y(v),class:'grid'}),svgElem('text',{x:left-8,y:y(v)+4,'text-anchor':'end'},fmt(v,3)));
 }
 entries.forEach((e,i)=>{
  const cx=left+(i+.5)*width;
  if(finite(e.value)) {
   const bar=svgElem('rect',{x:cx-width*.3,y:Math.min(y(0),y(e.value)),width:width*.6,height:Math.max(1,Math.abs(y(e.value)-y(0))),fill:e.run.color});
   const text=`${e.run.name}\n${label}: ${fmt(e.value)} ± ${e.st.std===null?'SD unavailable':fmt(e.st.std)} (n=${e.st.n})`;
   bar.append(svgElem('title',{},text));svg.append(bar);
   if(finite(e.st.std))svg.append(svgElem('line',{x1:cx,x2:cx,y1:y(e.value-e.st.std),y2:y(e.value+e.st.std),stroke:'#172b40','stroke-width':2}));
   svg.append(svgElem('text',{x:cx,y:Math.max(top,y(e.value+(e.st.std??0))-8),'text-anchor':'middle'},fmt(e.value)));
   hover.push({x:cx,y:y(e.value),text});
  } else svg.append(svgElem('text',{x:cx,y:top+15,'text-anchor':'middle'},'Unavailable'));
  const rest=xkey==='configuration'?[]:[...dimensions.keys()].filter(k=>k!==xkey&&new Set(runs.map(r=>fmt(coordinate(r,k)))).size>1)
     .map(k=>`${axisLabel(k)}=${fmt(coordinate(e.run,k))}`);
  const text=[fmt(e.label),...rest,`n=${e.st.n}`].join(' · ');
  const caption=svgElem('text',{x:cx,y:bottom+24,'text-anchor':'end',transform:`rotate(-28 ${cx} ${bottom+24})`}),lines=[];
  for(const part of text.split(/ · |, /)) {
   if(lines.length&&lines.at(-1).length+part.length<38)lines[lines.length-1]+=' · '+part;
   else lines.push(part);
  }
  lines.forEach((line,j)=>caption.append(svgElem('tspan',{x:cx,dy:j?14:0},line)));
  caption.append(svgElem('title',{},text));svg.append(caption);
 });
 svg.append(svgElem('text',{x:plotWidth/2,y:430,'text-anchor':'middle'},axisLabel(xkey)));
 attachHover(svg,hover);
}
function renderMemory(runs) {
 if(!hasMemory)return;
 const key=$('memory-metric').value;
 renderMetricBars(runs,key,memoryMetrics.find(m=>m[0]===key)[1],$('memory-chart'));
 scalarTable($('memory-table'),runs,memoryMetrics);
 const taskKey=$('bar-metric').value;
 const taskLabel=metrics.find(m=>m[0]===taskKey)[1];
 const points=runs.filter(r=>finite(r.metrics.gem5_aggressor_gbps)&&finite(r.metrics[taskKey]));
 const svg=chart($('traffic-task-chart'),`Achieved traffic versus ${taskLabel}`,720,380);
 const xd=domain(points.map(r=>r.metrics.gem5_aggressor_gbps),true);
 const yd=domain(points.map(r=>r.metrics[taskKey]),true);
 const a=axes(svg,xd,yd,720,380),hover=[];
 svg.append(svgElem('text',{x:12,y:16},taskLabel));
 svg.append(svgElem('text',{x:360,y:376,'text-anchor':'middle'},'Achieved aggressor traffic (GB/s)'));
 for(const r of points) {
  const x=a.x(r.metrics.gem5_aggressor_gbps), y=a.y(r.metrics[taskKey]);
  const text=`${r.name}\n${fmt(r.metrics.gem5_aggressor_gbps)} GB/s · ${taskLabel}: ${fmt(r.metrics[taskKey])}`;
  const point=svgElem('circle',{cx:x,cy:y,r:6,fill:r.color});
  point.append(svgElem('title',{},text));svg.append(point);
  hover.push({x,y,text});
 }
 attachHover(svg,hover);
 const baseline=data.runs.find(r=>r.completed&&r.config['hardware.aggressor_pattern']==='none');
 const rows=[['rmse_m','Tracking RMSE Δ (%)'],['path_m','Progress Δ (%)'],
             ['command_gap_p95_ms','Command gap p95 Δ (%)'],['gem5_cpu_dram_read_ns','Guest DRAM latency Δ (%)']];
 const comparable=r=>baseline&&JSON.stringify(baseline.provenance)===JSON.stringify(r.provenance)&&
   JSON.stringify(baseline.events)===JSON.stringify(r.events)&&
   [...new Set([...Object.keys(baseline.config),...Object.keys(r.config)])].every(k=>
     k.startsWith('hardware.aggressor_')||JSON.stringify(baseline.config[k])===JSON.stringify(r.config[k]));
 comparisonTable($('baseline-impact'),runs,runs.map(r=>Object.fromEntries(rows.map(([k])=>{
  const base=baseline?.metrics[k], value=r.metrics[k];
  return [k,comparable(r)&&finite(base)&&base!==0&&finite(value)?100*(value/base-1):null];
 }))),rows);
}
function renderBars(runs) {
 renderMemory(runs);
 const key=$('bar-metric').value;
 renderMetricBars(runs,key,metrics.find(m=>m[0]===key)[1],$('bar-chart'));
 scalarTable($('metrics-table'),runs,metrics);
 const notes=runs.flatMap(r=>(r.task_notes??[]).map(note=>`${r.name}: ${note}`));
 $('task-note').hidden=!notes.length;
 $('task-note').textContent=[...new Set(notes)].join('\n');
}
function renderTiming(runs) {
 const key=$('timing-metric').value;
 renderMetricBars(runs,key,timingMetrics.find(m=>m[0]===key)[1],$('timing-chart'));
 const count=runs.reduce((n,r)=>n+r.metric_stats[key].n,0);
 $('timing-note').textContent=count?`${count} repetition(s) with this metric. Rates are ratios of totals within each run, then averaged across repetitions. Error bars show sample SD.`:'No recorded timing data for the selected configurations. Local runs use collection throughput.';
 scalarTable($('timing-table'),runs,timingMetrics);
}
function renderArchitecture(runs) {
 const key=$('architecture-metric').value;
 renderMetricBars(runs,key,architectureMetrics.find(m=>m[0]===key)[1],$('architecture-chart'));
 scalarTable($('architecture-table'),runs,architectureMetrics);
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
 $('legend').replaceChildren();runs.forEach((r,i)=>{const item=elem('span');item.append(dot(r),document.createTextNode(`${r.name} (n=${r.count})`));$('legend').append(item);});
}
function render() {
 const runs=active();renderLibrary();$('headline').textContent=`${runs.length} configuration${runs.length===1?'':'s'} · ${runs.reduce((n,r)=>n+r.count,0)} repetitions`;
 $('empty').hidden=!!runs.length;$('content').hidden=!runs.length;$('export').disabled=!runs.length;
 try{history.replaceState(null,'','#runs='+runs.map(r=>r.id).join(','));}catch(_){}
 $('tooltip').hidden=true;if(!runs.length)return;renderConfig(runs);renderBars(runs);renderTiming(runs);renderArchitecture(runs);renderTraces(runs);
}
$('search').addEventListener('input',renderLibrary);
$('select-visible').addEventListener('click',()=>{visible().forEach(r=>selected.add(r.id));render();});
$('clear').addEventListener('click',()=>{selected.clear();render();});
for(const id of ['differences-only','provenance'])$(id).addEventListener('change',()=>renderConfig(active()));
$('x-axis').addEventListener('change',()=>{renderBars(active());renderTiming(active());renderArchitecture(active());});
$('architecture-metric').addEventListener('change',()=>renderArchitecture(active()));

$('timing-metric').addEventListener('change',()=>renderTiming(active()));
$('bar-metric').addEventListener('change',()=>renderBars(active()));
for(const id of ['signal','from','to'])$(id).addEventListener('change',()=>renderTraces(active()));
$('reset-time').addEventListener('click',()=>{$('from').value=0;$('to').value='';renderTraces(active());});
let expandedId=null, expandedButton=null;
const chartHomes={'signal-chart':$('signal-panel'),'trajectory-chart':$('trajectory-panel')};
function maximize(id, button) {
 expandedId=id;expandedButton=button;
 $('expanded-title').textContent=id==='signal-chart'?signals.find(([k])=>k===$('signal').value)[1]:'World trajectory (m)';
 $('expanded-plot').append($(id));$('expanded-chart').append($('tooltip'));
 document.body.style.overflow='hidden';
 $('expanded-chart').showModal();
}
$('maximize-signal').addEventListener('click',()=>maximize('signal-chart',$('maximize-signal')));
$('maximize-trajectory').addEventListener('click',()=>maximize('trajectory-chart',$('maximize-trajectory')));
$('close-expanded').addEventListener('click',()=>$('expanded-chart').close());
$('expanded-chart').addEventListener('close',()=>{
 if(expandedId)chartHomes[expandedId].append($(expandedId));
 expandedId=null;document.body.style.overflow='';document.body.append($('tooltip'));
 expandedButton?.focus();$('tooltip').hidden=true;
});
$('export').addEventListener('click',()=>{
 const rows=active().map(r=>({group_id:r.id,configuration:r.name,repetitions:r.count,members:r.path,completed:r.completed,timing_scope:data.timing_scope,
  ...Object.fromEntries(Object.entries(r.metric_stats).flatMap(([k,stats])=>Object.entries(stats).map(([stat,v])=>[k+'_'+stat,v]))),
  ...r.config,...r.provenance,parameter_events:JSON.stringify(r.events)}));
 const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];
 const quote=v=>{let s=v==null?'':String(v);if(typeof v==='string'&&/^\s*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
 const csv=[keys.map(quote).join(','),...rows.map(r=>keys.map(k=>quote(r[k])).join(','))].join('\r\n');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=elem('a');a.href=url;a.download='selected_groups.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
render();

// Refit bars when the viewport changes, including when a hidden chart returns.
if(typeof ResizeObserver!=='undefined') {
 for(const [id,redraw] of [['bar-chart',renderBars],['timing-chart',renderTiming],['architecture-chart',renderArchitecture]]) {
  let width=$(id).clientWidth;
  new ResizeObserver(()=>{
   const next=$(id).clientWidth;
   if(next===width)return;
   width=next;
   if(next&&active().length)redraw(active());
  }).observe($(id));
 }
}
