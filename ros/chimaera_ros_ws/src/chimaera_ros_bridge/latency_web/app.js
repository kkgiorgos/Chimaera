'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = value => JSON.parse(JSON.stringify(value));
const modeName = mode => ({native_free:'Native running',native_stepped:'Native stepped',chimaera:'Chimaera'}[mode] || mode);
const statusName = status => ({complete:'Complete',running:'Running',preparing:'Preparing',pending:'Pending',failed:'Failed',stopped:'Stopped',stopping:'Stopping',interrupted:'Interrupted'}[status] || status);
const profileName = config => config.gem5_name || (config.cpu || config.cpu_type || 'kvm').toUpperCase();
const fmt = (value, digits=2) => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString(undefined,{maximumFractionDigits:digits});
const state = {snapshot:null,connected:false,draft:null,plan:null,view:'configure',selected:null,
  report:null,reportKey:'',direction:'host_to_guest',pair:null,detail:null,detailToken:0,samples:new Map(),
  logPinned:false,logCase:null,logsToken:0,planToken:0,editor:'form',runtimeSignature:'',details:[]};
let planTimer;
async function api(path, body) {
  const response = await fetch('/api/'+path, body === undefined ? {cache:'no-store'} : {
    method:'POST',headers:{'Content-Type':'application/json','X-Chimaera-Request':'1'},body:JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'The request failed.');
  return value;
}
function error(message) { $('error-banner').hidden=false; $('error-banner').querySelector('span').textContent=message; }
function selectedJob() { return state.snapshot?.jobs.find(job=>job.id===state.selected); }
function activeJob() { return state.snapshot?.jobs.find(job=>['running','preparing','stopping'].includes(job.status)); }
function remember() { try {localStorage.setItem('chimaera-latency-draft',JSON.stringify(state.draft));} catch {} }
function setView(view) {
  state.view=view;
  for (const name of ['configure','monitor','results']) $(name+'-view').hidden=name!==view;
  document.querySelectorAll('.nav-button').forEach(button=>button.classList.toggle('active',button.dataset.view===view));
  $('view-title').textContent={configure:'Configure',monitor:'Run monitor',results:'Results'}[view];
  if (view==='monitor') {renderMonitor(); void fetchLogs();}
  if (view==='results') void loadReport(true);
}
function download(value, name, type='application/json') {
  const url=URL.createObjectURL(new Blob([typeof value==='string'?value:JSON.stringify(value,null,2)+'\n'],{type}));
  const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),500);
}
function makeOptions(select, items, formatter=value=>value) {
  const old=select.value;
  select.innerHTML=items.map(item=>`<option value="${esc(item)}">${esc(formatter(item))}</option>`).join('');
  if (items.map(String).includes(old)) select.value=old;
}
function themeControls() {
  const focused=document.activeElement?.dataset.accent;
  $('flavor').value=benchTheme.flavor;
  $('accent-swatches').innerHTML=benchTheme.accents.map(accent=>`<button type="button" aria-label="${accent} accent" title="${accent}" aria-pressed="${accent===benchTheme.accent}" data-accent="${accent}" style="background:#${benchTheme.palette[accent]}"></button>`).join('');
  if(focused)$('accent-swatches').querySelector(`[data-accent="${focused}"]`)?.focus({preventScroll:true});
}
function renderForm() {
  const config=state.draft;
  $('sweep-name').value=config.name;
  document.querySelectorAll('[data-list]').forEach(input=>{input.value=config[input.dataset.list].map(v=>v/Number(input.dataset.scale||1)).join(', ');});
  document.querySelectorAll('[data-value]').forEach(input=>{input.value=config[input.dataset.value];});
  document.querySelectorAll('[data-mode]').forEach(input=>{input.checked=config.modes.includes(input.dataset.mode);});
  $('reliability').value=config.reliability;
  $('physics-step').value=config.physics_step_ns/1e6;
  $('workload').value=config.workload||'synthetic';
  document.querySelectorAll('[data-ball]').forEach(input=>input.value=config.ball_catching[input.dataset.ball]);
  renderWorkload();
  $('config-json').value=JSON.stringify(config,null,2);
  renderProfiles();
}
function renderWorkload() {
  const ball=state.draft.workload==='ball_catching';
  $('ball-settings').hidden=!ball;
  document.querySelectorAll('[data-synthetic]').forEach(field=>field.hidden=ball);
}
function renderProfiles() {
  $('profiles').innerHTML=state.draft.gem5_configs.map((profile,index)=>`<section class="profile" aria-label="Gem5 configuration ${index+1}">
    <div class="profile-heading"><input aria-label="Configuration ${index+1} name" data-profile="${index}" data-setting="name" value="${esc(profile.name)}"><button type="button" data-remove-profile="${index}" aria-label="Remove ${esc(profile.name)}" ${state.draft.gem5_configs.length===1?'disabled':''}>Remove</button></div>
    <div class="field-grid"><label>CPU model<select data-profile="${index}" data-setting="cpu_type">${[['kvm','KVM'],['timing','TimingSimpleCPU'],['o3','O3']].map(([key,name])=>`<option value="${key}" ${profile.cpu_type===key?'selected':''}>${name}</option>`).join('')}</select></label><label>CPU clock<input data-profile="${index}" data-setting="cpu_clock" value="${esc(profile.cpu_clock)}" placeholder="3GHz"></label><label>Core count<input type="number" min="1" max="64" data-profile="${index}" data-setting="num_cores" value="${profile.num_cores}"></label></div>
    <details class="hardware-extra"><summary>Cache sizes and associativity</summary><div class="field-grid">${[['l1d_size','L1 data'],['l1i_size','L1 instruction'],['l2_size','L2 size']].map(([key,label])=>`<label>${label}<input data-profile="${index}" data-setting="${key}" value="${esc(profile[key])}" placeholder="32KiB"></label>`).join('')}${[['l1_assoc','L1 ways'],['l2_assoc','L2 ways']].map(([key,label])=>`<label>${label}<input type="number" min="1" max="64" data-profile="${index}" data-setting="${key}" value="${profile[key]}"></label>`).join('')}</div></details></section>`).join('');
}
async function editorMode(mode) {
  if(mode==='form'&&state.editor==='json'){
    clearTimeout(planTimer);await validatePlan();if(!state.plan)return;
  }
  state.editor=mode;$('form-editor').hidden=mode!=='form';$('json-editor').hidden=mode!=='json';
  for (const name of ['form','json']) {$(name+'-mode').classList.toggle('selected',mode===name);$(name+'-mode').setAttribute('aria-pressed',String(mode===name));}
  if (mode==='json') {$('config-json').value=JSON.stringify(state.draft,null,2);schedulePlan();}
  else renderForm();
}
function planError(message) {
  clearTimeout(planTimer);++state.planToken;
  state.plan=null;$('plan-error').textContent=message;$('plan-error').hidden=false;updateActions();
}
function schedulePlan() {
  ++state.planToken;
  remember();if (state.editor==='form') $('config-json').value=JSON.stringify(state.draft,null,2);
  clearTimeout(planTimer);planTimer=setTimeout(()=>void validatePlan(),220);
  state.plan=null;updateActions();
}
async function validatePlan() {
  const token=++state.planToken;
  try {const config=state.editor==='json'?JSON.parse($('config-json').value):state.draft;
    const plan=await api('plan',config);if(token!==state.planToken)return;
    state.draft=plan.config;remember();
    state.plan=plan;$('plan-error').hidden=true;renderPlan();updateActions();
  } catch (failure) {if(token===state.planToken)planError(failure.message);}
}
function matrix(intervals,polls,cell) {
  let content=`<div class="matrix" style="--columns:${polls.length}"><div class="axis-label">Sync, ms</div>`;
  content+=polls.map(poll=>`<div class="axis-label">${fmt(poll/1000,3)}</div>`).join('');
  for (const interval of intervals) {content+=`<div class="axis-label row-label">${fmt(interval/1000,3)}</div>`;
    for (const poll of polls) content+=cell(interval,poll);}
  return content+'</div>';
}
function renderPlan() {
  const {config,total,counts,warnings,simulated_seconds}=state.plan;
  const perPair=config.payload_bytes.length*config.rate_hz.length*config.repeats*config.gem5_configs.length;
  $('plan-matrix').innerHTML=matrix(config.interval_us,config.poll_us,(interval,poll)=>
    poll>=interval?'<div class="matrix-cell hatched" title="Polling must be shorter than synchronization">Excluded</div>':
    `<div class="matrix-cell ${counts.chimaera?'planned':''}"><span>${counts.chimaera?perPair:'—'}</span><small>${counts.chimaera?'cases':'Native controls'}</small></div>`);
  $('plan-total').textContent=fmt(total,0);
  $('plan-counts').innerHTML=Object.entries(counts).map(([mode,count])=>`<div><dt>${modeName(mode)}</dt><dd>${count}</dd></div>`).join('');
  $('plan-duration').textContent=fmt(simulated_seconds,2)+' simulated seconds in total, excluding startup and record export.';
  const streams=state.plan.traffic||[];
  $('traffic-preview').hidden=!streams.length;
  $('traffic-preview').innerHTML=streams.length?'<h3>Concurrent ball-catching traffic</h3><table><thead><tr><th>Stream</th><th>Direction</th><th>ROS type</th><th>Requested Hz</th></tr></thead><tbody>'+streams.map(s=>`<tr><td>${esc(s.label)}</td><td>${s.direction==='host_to_guest'?'Host → guest':'Guest → host'}</td><td>${esc(s.type.split('/').at(-1))}</td><td>${fmt(s.rate_hz)}</td></tr>`).join('')+`</tbody></table><p class="field-note">${fmt(streams.filter(s=>s.body_bytes).reduce((n,s)=>n+s.rate_hz*s.body_bytes,0)/1024**2)} MiB/s of RGB data requested. Images use paired replacement and an age limit; small messages retain the example’s QoS. Readiness flags add 25 / 10 Hz background traffic. Results separate the five measured streams.</p>`:'';
  $('plan-notes').hidden=!warnings.length;
  $('plan-notes').innerHTML=warnings.map(warning=>`<p>${esc(warning)}</p>`).join('')+
    (warnings.length?`<p>Requested per direction: ${config.rate_hz.map(rate=>fmt(rate*config.duration,0)).join(' / ')} messages per case. Actual delivery counts remain visible in results.</p>`:'');
  const cases=state.plan.cases.slice(0,100);
  $('planned-cases').innerHTML=caseTable(cases,false)+(total>100?`<p class="field-note">Showing the first 100 of ${total} cases.</p>`:'');
}
function updateActions() {
  const busy=!!activeJob(),runtime=state.snapshot?.runtime;
  const needsGem5=state.plan?.config.modes.includes('chimaera')??true;
  const ready=runtime?.ready_native && (!needsGem5 || runtime.ready_chimaera&&runtime.supports_cpu_configs);
  $('start-sweep').disabled=!state.plan||busy||!state.connected||!ready;
  $('start-sweep').querySelector('span').textContent=busy?'Runner busy':state.plan?`Run ${state.plan.total} cases`:'Run sweep';
  $('start-sweep').title=ready?'Start this sweep':needsGem5?'Open Runtime to check the build, guest bundle and KVM.':'Open Runtime to check the benchmark build.';
  $('prepare-runtime').disabled=busy||!runtime?.can_build;
  $('stage-runtime').disabled=busy||!runtime?.ready_native;
  $('runtime-form').querySelector('button[type=submit]').disabled=busy;
  $('running-dot').hidden=!busy;
}
function caseTable(cases, interactive=true) {
  return `<table><thead><tr><th>Case</th><th>Mode / configuration</th><th>Body</th><th>Rate</th><th>Sync / poll</th><th>Repeat</th>${interactive?'<th>Status</th>':''}</tr></thead><tbody>`+
    cases.map(item=>{const c=item.config;return `<tr class="${item.status==='running'?'current-row':''}"><td>${interactive?`<button class="case-button" data-log-case="${esc(item.id)}">${esc(item.id)}</button>`:esc(item.id)}</td><td>${modeName(c.mode)}${c.mode==='chimaera'?`<small>${esc(c.gem5_name||profileName(c))}</small>`:''}</td><td>${fmt(c.payload_bytes,0)} B</td><td>${fmt(c.rate_hz)} Hz</td><td>${c.mode==='native_free'?'—':fmt(c.interval_us/1000)+' / '+(c.mode==='chimaera'?fmt(c.poll_us/1000):'—')+' ms'}</td><td>${c.repeat}</td>${interactive?`<td><span class="status status-${esc(item.status)}">${statusName(item.status)}</span></td>`:''}</tr>`;}).join('')+'</tbody></table>';
}
function renderRuntime() {
  const runtime=state.snapshot.runtime;
  const ready=runtime.ready_chimaera&&runtime.supports_cpu_configs;
  $('runtime-status').textContent=activeJob()?.kind==='prepare'?'Preparing runtime':ready?'Runtime ready':runtime.ready_native?'Native runtime ready':'Runtime needs preparation';
  $('runtime-toggle').classList.toggle('unready',!ready);
  $('prerequisites').innerHTML=runtime.checks.map(check=>`<span class="prerequisite ${check.ready?'':'missing'}">${esc(check.label)}</span>`).join('');
  const signature=JSON.stringify(runtime.paths);
  if (signature!==state.runtimeSignature&&!$('runtime-form').contains(document.activeElement)) {
    for (const [key,path] of Object.entries(runtime.paths)) $('runtime-form').elements.namedItem(key).value=path;
    state.runtimeSignature=signature;
  }
  updateActions();
}
function renderHistory() {
  const jobs=state.snapshot.jobs;
  const markup=jobs.length?jobs.map(job=>`<button class="history-item ${job.id===state.selected?'selected':''}" data-job="${job.id}"><strong>${esc(job.name)}</strong><small><i class="history-dot ${job.status}"></i>${statusName(job.status)}${job.total?' / '+job.completed+' of '+job.total:''}</small><small>${new Date(job.created*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}${job.kind==='import'?' / imported':''}</small></button>`).join(''):'<p class="muted">Run a sweep, or import earlier results.</p>';
  if($('history').innerHTML!==markup)$('history').innerHTML=markup;
}
async function refresh() {
  try {
    state.snapshot=await api('state');state.connected=true;
    if (!state.selected||!state.snapshot.jobs.some(job=>job.id===state.selected)) state.selected=activeJob()?.id||state.snapshot.jobs[0]?.id||null;
    $('connection').classList.remove('offline');$('connection').lastChild.textContent='Local runner connected';
    renderRuntime();renderHistory();
    if(state.view==='monitor'){renderMonitor();void fetchLogs();}
    if(state.view==='results')void loadReport();
  } catch (failure) {
    state.connected=false;$('connection').classList.add('offline');$('connection').lastChild.textContent='Runner disconnected';updateActions();
  }
}
async function selectJob(identifier,view='monitor') {
  state.selected=identifier;state.reportKey='';state.report=null;state.pair=null;state.detail=null;state.logPinned=false;state.logCase=null;
  renderHistory();setView(view);
}
function renderMonitor() {
  const job=selectedJob();$('monitor-empty').hidden=!!job;$('monitor-content').hidden=!job;
  $('clone-sweep').disabled=!job?.config;
  if(!job)return;
  $('monitor-name').textContent=job.name;
  $('monitor-description').textContent=job.kind==='prepare'?'Build output and guest staging progress.':`${job.total} cases. The frozen configuration is retained with these results.`;
  $('job-status').textContent=statusName(job.status);$('job-status').className='status status-'+job.status;
  $('case-progress').textContent=job.kind==='prepare'?'Preparing the runtime':`${job.completed} of ${job.total} cases complete`;
  $('phase-description').textContent=job.progress?`${job.progress.case}: ${job.progress.phase}`:job.status==='complete'?'All records collected':job.status==='stopping'?'Waiting for owned processes to stop':'';
  $('simulation-progress').textContent=job.progress?.simulated_seconds!=null?fmt(job.progress.simulated_seconds,3)+' simulated s':'';
  if(job.kind==='prepare'&&['preparing','stopping'].includes(job.status))$('job-progress').removeAttribute('value');
  else $('job-progress').value=job.kind==='prepare'?Number(job.status==='complete'):job.total?job.completed/job.total:0;
  $('stop-job').hidden=!['running','preparing','stopping'].includes(job.status);
  $('stop-job').disabled=job.status==='stopping';
  $('run-error').hidden=!job.error&&!job.report_error;$('run-error').textContent=job.error||job.report_error||'';
  const resumable=job.kind==='sweep'&&['failed','stopped','interrupted'].includes(job.status)&&job.resume_timeout;
  $('resume-panel').hidden=!resumable;
  if(resumable){
    if($('resume-timeout').dataset.job!==job.id){
      $('resume-timeout').value=job.resume_timeout;
      $('resume-timeout').dataset.job=job.id;
    }
    $('resume-job').disabled=!!activeJob()||!state.connected;
    $('resume-job').textContent=`Resume ${job.total-job.completed} cases`;
    $('resume-note').textContent=`Reuse ${job.completed} completed cases and retry the ${job.total-job.completed} unfinished cases. Previous failed attempts and logs are preserved. Measurements and synchronization settings stay the same.`;
  }
  $('case-table').hidden=job.kind==='prepare';const table=job.kind==='prepare'?'':caseTable(job.cases);
  if($('case-table').innerHTML!==table)$('case-table').innerHTML=table;
  $('log-case').hidden=job.kind==='prepare';$('log-stream').hidden=job.kind==='prepare';
  if(job.kind!=='prepare'){
    const old=$('log-case').value;
    makeOptions($('log-case'),job.cases.map(c=>c.id));
    if(!state.logPinned){const latest=job.cases.find(c=>c.status==='running')||job.cases.filter(c=>c.status!=='pending').at(-1)||job.cases[0];if(latest)$('log-case').value=latest.id;}
    else if(job.cases.some(c=>c.id===old))$('log-case').value=old;
  }
}
async function fetchLogs() {
  const job=selectedJob();if(!job||state.view!=='monitor')return;
  const token=++state.logsToken,caseId=$('log-case').value,stream=$('log-stream').value;
  try{const result=await api(`jobs/${job.id}/logs?case=${encodeURIComponent(caseId)}&stream=${stream}`);
    if(token!==state.logsToken)return;
    const pre=$('logs'),atBottom=pre.scrollHeight-pre.scrollTop-pre.clientHeight<35;
    pre.textContent=result.text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g,'').replace(/\r/g,'');
    if(atBottom)pre.scrollTop=pre.scrollHeight;
    $('log-note').textContent=result.truncated?'Showing the most recent 64 KiB. Full logs stay in the sweep directory.':'Full log output so far.';
  }catch(failure){if(token===state.logsToken)$('logs').textContent=failure.message;}
}
async function loadReport(force=false) {
  const job=selectedJob();
  const key=job?`${job.id}:${job.completed}:${job.status}`:'';
  if(!force&&key===state.reportKey)return;
  state.reportKey=key;
  if(!job||job.kind==='prepare') {state.report=null;renderResults();return;}
  try{const report=await api(`jobs/${job.id}/report`);if(state.selected!==job.id)return;state.report=report;renderResults();}
  catch(failure){error(failure.message);}
}
function unique(values) {return [...new Set(values)];}
function streamKey(){return $('filter-stream').value||'';}
function runMetrics(run,direction=state.direction){return Object.keys(run.streams||{}).length?run.streams[streamKey()]:run.directions[direction];}
function renderStreamFilter(){
  const streams=Object.values(state.report.runs.find(r=>Object.keys(r.streams||{}).length)?.streams||{}).filter(s=>s.direction===state.direction);
  $('stream-filter-field').hidden=!streams.length;
  makeOptions($('filter-stream'),streams.length?streams.map(s=>s.name):[''],name=>{const stream=streams.find(s=>s.name===name);return stream?`${stream.label} / ${stream.type} / ${fmt(stream.rate_hz)} Hz`:'Synthetic';});
}
function renderResults() {
  const job=selectedJob(),report=state.report,has=!!report?.runs.length;
  $('results-empty').hidden=has;$('results-content').hidden=!has;
  for(const id of ['export-csv','export-results'])$(id).hidden=!has;
  if(!has)return;
  $('results-description').textContent=`${job.name}. ${report.runs.length} complete cases; ${report.excluded.length} excluded. All values use simulation time.`;
  $('export-csv').href=`/api/jobs/${job.id}/export?format=csv`;$('export-results').href=`/api/jobs/${job.id}/export`;
  const chimaera=report.runs.filter(run=>run.config.mode==='chimaera');
  makeOptions($('filter-profile'),unique(chimaera.map(run=>profileName(run.config))));
  makeOptions($('filter-body'),unique(report.runs.map(run=>run.config.payload_bytes)),v=>fmt(v,0)+' B');
  makeOptions($('filter-rate'),unique(report.runs.map(run=>run.config.rate_hz)),v=>fmt(v)+' Hz');
  makeOptions($('filter-repeat'),['all',...unique(report.runs.map(run=>run.config.repeat))],v=>v==='all'?'All repetitions':'Repeat '+v);
  const ball=report.runs.some(run=>run.config.workload==='ball_catching');
  $('body-filter-label').textContent=ball?'RGB bytes per image':'Body size';
  $('rate-filter-label').textContent=ball?'Camera rate':'Publish rate';
  renderHeatmaps();
  const rows=report.runs.flatMap(run=>{
    const measurements=Object.keys(run.streams||{}).length?Object.values(run.streams).map(m=>[m.direction,m.name,m]):Object.entries(run.directions).map(([d,m])=>[d,'',m]);
    return measurements.map(([direction,stream,metrics])=>`<tr><td><button class="case-button" data-result-case="${esc(run.case_id)}" data-result-direction="${direction}" data-result-stream="${stream}">${esc(run.case_id)}</button></td><td>${modeName(run.config.mode)}<small>${run.config.mode==='chimaera'?esc(profileName(run.config)):''}</small></td><td>${direction==='host_to_guest'?'Host → guest':'Guest → host'}<small>${esc(metrics.label||'Synthetic')}</small></td><td>${stream?fmt(metrics.serialized_bytes,0)+' CDR B / '+fmt(metrics.rate_hz):fmt(run.config.payload_bytes,0)+' B / '+fmt(run.config.rate_hz)} Hz</td><td>${fmt(metrics.latency.mean_ms)}</td><td>${fmt(metrics.latency.p95_ms)}</td><td>${fmt(metrics.latency.p99_ms)}</td><td>${metrics.received} / ${metrics.published}</td></tr>`);
  });
  $('results-table').innerHTML='<table><thead><tr><th>Case</th><th>Mode</th><th>Direction</th><th>Traffic</th><th>Mean, ms</th><th>p95, ms</th><th>p99, ms</th><th>Received / sent</th></tr></thead><tbody>'+rows.join('')+'</tbody></table>';
}
function candidates() {
  if(!state.report)return [];
  return state.report.runs.filter(run=>runMetrics(run)&&run.config.mode==='chimaera'&&profileName(run.config)===$('filter-profile').value&&
    String(run.config.payload_bytes)===$('filter-body').value&&String(run.config.rate_hz)===$('filter-rate').value&&
    ($('filter-repeat').value==='all'||String(run.config.repeat)===$('filter-repeat').value));
}
function cellValue(run,reference) {
  const metric=$('statistic').value;
  if(reference==='absolute')return runMetrics(run).latency[metric];
  const comparison=state.report.comparisons.find(item=>item.run_id===run.config.run_id&&item.baseline===reference&&item.direction===state.direction&&(item.stream||'')===streamKey());
  return comparison?.differences_ms[metric]??null;
}
function colorFor(value,maximum) {
  const p=benchTheme.palette,base=benchTheme.rgb(p.base),positive=state.direction==='host_to_guest'?p.blue:p.peach;
  const color=benchTheme.rgb(value<0?p.teal:positive),ratio=.13+.77*Math.min(1,Math.abs(value)/(maximum||1));
  const mixed=base.map((v,i)=>Math.round(v*(1-ratio)+color[i]*ratio));
  const hex=mixed.map(v=>v.toString(16).padStart(2,'0')).join('');
  const ink=[p.text,'11111b','ffffff'].sort((a,b)=>benchTheme.contrast(b,hex)-benchTheme.contrast(a,hex))[0];
  return `background:#${hex};color:#${ink}`;
}
function renderHeatmaps() {
  if(!state.report?.runs.length)return;
  renderStreamFilter();
  const focused=document.activeElement?.dataset.pair,focusedReference=document.activeElement?.dataset.reference;
  const runs=candidates(),reference=$('reference').value;
  const references=reference==='both'?['native_free','native_stepped']:[reference];
  const intervals=unique(runs.map(r=>r.config.interval_us)).sort((a,b)=>b-a),polls=unique(runs.map(r=>r.config.poll_us)).sort((a,b)=>b-a);
  if(!runs.some(run=>run.config.interval_us+':'+run.config.poll_us===state.pair))state.pair=runs.length?runs[0].config.interval_us+':'+runs[0].config.poll_us:null;
  const values=[];for(const run of runs)for(const ref of references){const v=cellValue(run,ref);if(v!=null&&Number.isFinite(v))values.push(v);}
  const max=Math.max(0,...values.map(Math.abs));
  $('result-heatmaps').classList.toggle('single',references.length===1);
  $('result-heatmaps').innerHTML=runs.length?references.map(ref=>`<section class="heatmap"><div class="heatmap-heading"><h2>${ref==='absolute'?'Absolute latency':'Added vs '+(ref==='native_free'?'native running':'native stepped')}</h2><small>${$('statistic').selectedOptions[0].text}, ms</small></div><div class="axis-description">Polling interval (ms)</div>`+matrix(intervals,polls,(interval,poll)=>{
    const group=runs.filter(run=>run.config.interval_us===interval&&run.config.poll_us===poll);
    const nums=group.map(run=>cellValue(run,ref)).filter(v=>v!=null&&Number.isFinite(v));
    if(!nums.length)return `<button type="button" class="matrix-cell hatched" disabled title="${poll>=interval?'Polling must be shorter than synchronization':'No matched measurements or native baseline'}">${poll>=interval?'Excluded':'No match'}</button>`;
    const value=nums.reduce((a,b)=>a+b,0)/nums.length,pair=interval+':'+poll;
    return `<button type="button" class="matrix-cell ${state.pair===pair?'selected':''}" data-pair="${pair}" data-reference="${ref}" aria-pressed="${state.pair===pair}" style="${colorFor(value,max)}" title="${interval/1000} ms sync, ${poll/1000} ms polling. ${fmt(value,3)} simulation ms; ${nums.length} run summaries."><span>${value>0&&ref!=='absolute'?'+':''}${fmt(value)}</span><small>${nums.length} ${nums.length===1?'run':'runs'}</small></button>`;
  })+'</section>').join(''):'<div class="method-block"><h3>No Chimaera cases in this selection</h3><p>Native results are available in the case table and distribution view below.</p></div>';
  $('heatmap-legend').innerHTML=`<span>0 ms</span><i style="background:linear-gradient(90deg,rgb(var(--ctp-base)),rgb(var(--ctp-${state.direction==='host_to_guest'?'blue':'peach'})))"></i><span>${fmt(max)} ms magnitude</span>`;
  $('aggregation-note').textContent=($('filter-repeat').value==='all'?'Cells average the selected run statistic across repetitions. ':'Each cell shows one repetition. ')+
    'Unmatched cases stay empty. Negative differences use teal. Native controls have physics-step granularity.';
  const chosen=runs.filter(run=>run.config.interval_us+':'+run.config.poll_us===state.pair);
  if(!chosen.length&&runs.length)state.pair=runs[0].config.interval_us+':'+runs[0].config.poll_us;
  renderDetailChoices();
  if(focused)$('result-heatmaps').querySelector(`[data-pair="${focused}"][data-reference="${focusedReference}"]`)?.focus({preventScroll:true});
}
function renderDetailChoices(preferred) {
  let runs=candidates().filter(run=>run.config.interval_us+':'+run.config.poll_us===state.pair);
  if(!runs.length&&!state.report.runs.some(run=>run.config.mode==='chimaera'))runs=state.report.runs.filter(run=>runMetrics(run)&&
    String(run.config.payload_bytes)===$('filter-body').value&&String(run.config.rate_hz)===$('filter-rate').value&&
    ($('filter-repeat').value==='all'||String(run.config.repeat)===$('filter-repeat').value));
  document.querySelector('.detail-section').hidden=!runs.length;
  makeOptions($('detail-case'),runs.map(run=>run.case_id),id=>{const run=runs.find(r=>r.case_id===id);return `${id} / repeat ${run.config.repeat}`;});
  if(preferred&&runs.some(run=>run.case_id===preferred))$('detail-case').value=preferred;
  void renderDetail();
}
function legendName(run) {return run.config.mode==='chimaera'?profileName(run.config):modeName(run.config.mode);}
async function renderDetail() {
  const report=state.report,job=selectedJob(),caseId=$('detail-case').value;
  if(!report||!job)return;
  const run=report.runs.find(r=>r.case_id===caseId);if(!run)return;
  state.detail=run;const direction=state.direction,stream=streamKey(),token=++state.detailToken,metrics=runMetrics(run),latency=metrics.latency;
  $('detail-heading').textContent=`${legendName(run)} / ${run.config.mode==='native_free'?'normal execution':fmt(run.config.interval_us/1000)+' ms sync, '+fmt(run.config.poll_us/1000)+' ms polling'}`;
  $('detail-description').textContent=`Repeat ${run.config.repeat}. `+(stream?`${metrics.label}: ${fmt(metrics.serialized_bytes,0)} serialized bytes at ${fmt(metrics.rate_hz)} Hz. `:`${fmt(run.config.payload_bytes,0)} byte body at ${fmt(run.config.rate_hz)} Hz. `)+`${state.direction==='host_to_guest'?'Host → guest':'Guest → host'}.`;
  $('detail-stats').innerHTML=[['Mean',fmt(latency.mean_ms),'ms'],['Median',fmt(latency.p50_ms),'ms'],['p95',fmt(latency.p95_ms),'ms'],['p99',fmt(latency.p99_ms),'ms'],['Received',`${metrics.received} / ${metrics.published}`, ''],['Missing at drain',metrics.missing_at_drain,'']].map(([name,value,unit])=>`<div><span>${name}</span><strong>${value}</strong><small>${unit}</small></div>`).join('');
  $('raw-events').href=`/api/jobs/${job.id}/events?case=${encodeURIComponent(caseId)}`;
  const peers=[run];
  for(const ref of ['native_free','native_stepped']){
    const cmp=report.comparisons.find(c=>c.run_id===run.config.run_id&&c.baseline===ref&&c.direction===state.direction&&(c.stream||'')===stream);
    const baseline=cmp&&report.runs.find(r=>r.config.run_id===cmp.baseline_id);if(baseline)peers.push(baseline);
  }
  $('sample-note').textContent='Loading raw observations…';
  const results=await Promise.allSettled(peers.map(async peer=>{
    const key=job.id+':'+peer.case_id+':'+direction+':'+stream;
    if(!state.samples.has(key))state.samples.set(key,await api(`jobs/${job.id}/samples?case=${encodeURIComponent(peer.case_id)}&direction=${direction}&stream=${encodeURIComponent(stream)}`));
    return {run:peer,...state.samples.get(key)};
  }));
  if(token!==state.detailToken)return;
  state.details=results.filter(item=>item.status==='fulfilled').map(item=>item.value);
  drawPlots();
  const failures=results.filter(item=>item.status==='rejected').map(item=>item.reason.message);
  $('sample-note').textContent=(metrics.received<10000?`${metrics.received} receipts: this run has limited evidence for latency tails. `:'')+
    'CDF and time plots retain at most 2,000 points; summary statistics use every received message. Missing messages are censored at the drain boundary. '+(stream?'Paired image replacement, age rejection and DDS queue depth can remove samples. ':'')+
    (metrics.native_clock_step_ms!=null?`Native clock step: ${fmt(metrics.native_clock_step_ms,3)} ms. `:'')+(failures.length?failures.join(' '):'');
}
function drawPlots() {
  const palette=benchTheme.palette,colors=[state.direction==='host_to_guest'?palette.blue:palette.peach,palette.overlay2,palette.teal];
  const datasets=state.details.map((data,i)=>({...data,color:'#'+colors[i],dash:i===1?'5 4':i===2?'2 3':''}));
  $('plot-legend').innerHTML=datasets.map((data,i)=>`<span><i style="color:${data.color};${i?'border-top-style:dashed':''}"></i>${esc(legendName(data.run))} (${fmt(data.count,0)})</span>`).join('');
  plot($('cdf-chart'),datasets,'cdf');plot($('trace-chart'),datasets,'trace');
}
function plot(svg,datasets,kind) {
  const width=Math.max(300,svg.clientWidth),height=244,left=47,right=12,top=17,bottom=36;
  svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
  const points=datasets.flatMap(data=>data[kind]);
  if(!points.length){svg.innerHTML=`<text x="${width/2}" y="120" text-anchor="middle">No received samples in this case</text>`;return;}
  const xmax=Math.max(.001,...points.map(p=>p[0]))*1.04,ymax=kind==='cdf'?1:Math.max(.001,...points.map(p=>p[1]))*1.08;
  const x=v=>left+v/xmax*(width-left-right),y=v=>height-bottom-v/ymax*(height-top-bottom);
  let content='';
  for(let i=0;i<=4;i++){
    const xv=xmax*i/4,yv=ymax*i/4;
    content+=`<line x1="${left}" y1="${y(yv)}" x2="${width-right}" y2="${y(yv)}" stroke="rgb(var(--ctp-surface0))"/><text x="${left-8}" y="${y(yv)+3}" text-anchor="end">${kind==='cdf'?Math.round(yv*100)+'%':fmt(yv,1)}</text><text x="${x(xv)}" y="${height-bottom+19}" text-anchor="middle">${fmt(xv,kind==='trace'?2:1)}</text>`;
  }
  for(const data of datasets){
    if(!data[kind].length)continue;
    let path=kind==='cdf'?`M${x(data[kind][0][0])},${y(0)}`:'';
    data[kind].forEach((p,i)=>{path+=kind==='cdf'?`H${x(p[0])}V${y(p[1])}`:`${i?'L':'M'}${x(p[0])},${y(p[1])}`;});
    content+=`<path d="${path}" fill="none" stroke="${data.color}" stroke-width="1.8" stroke-dasharray="${data.dash}" stroke-linejoin="round"/>`;
  }
  content+=`<text x="${width-right}" y="${height-2}" text-anchor="end">${kind==='cdf'?'Latency (simulation ms)':'Publication time (simulation s)'}</text>`;
  svg.innerHTML=content;
}
async function startSweep() {
  try{const job=await api('start',state.plan.config);await refresh();await selectJob(job.id);}
  catch(failure){error(failure.message);}
}
async function prepare(build) {
  try{const job=await api('prepare',{build});await refresh();await selectJob(job.id);}
  catch(failure){error(failure.message);}
}
function openImport() {$('import-dialog').showModal();$('import-path').focus();}
document.addEventListener('click',event=>{
  const button=event.target.closest('button,a');if(!button)return;
  if(button.dataset.view){setView(button.dataset.view);return;}
  if(button.dataset.job){void selectJob(button.dataset.job,state.snapshot.jobs.find(j=>j.id===button.dataset.job).status==='complete'&&state.snapshot.jobs.find(j=>j.id===button.dataset.job).kind!=='prepare'?'results':'monitor');return;}
  if(button.dataset.logCase){state.logPinned=true;$('log-case').value=button.dataset.logCase;void fetchLogs();return;}
  if(button.dataset.pair){state.pair=button.dataset.pair;renderHeatmaps();return;}
  if(button.dataset.direction){state.direction=button.dataset.direction;document.querySelectorAll('[data-direction]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});renderHeatmaps();return;}
  if(button.dataset.resultCase){
    state.direction=button.dataset.resultDirection;renderStreamFilter();$('filter-stream').value=button.dataset.resultStream||'';const run=state.report.runs.find(r=>r.case_id===button.dataset.resultCase);
    document.querySelectorAll('[data-direction]').forEach(b=>{const selected=b.dataset.direction===state.direction;b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected));});
    if(run.config.mode==='chimaera'){$('filter-profile').value=profileName(run.config);$('filter-body').value=run.config.payload_bytes;$('filter-rate').value=run.config.rate_hz;$('filter-repeat').value=run.config.repeat;state.pair=run.config.interval_us+':'+run.config.poll_us;renderHeatmaps();}
    makeOptions($('detail-case'),state.report.runs.map(r=>r.case_id));$('detail-case').value=run.case_id;void renderDetail();$('detail-heading').scrollIntoView({block:'start'});return;
  }
  if(button.dataset.accent){benchTheme.apply(benchTheme.flavor,button.dataset.accent);themeControls();return;}
  if(button.dataset.removeProfile!=null){state.draft.gem5_configs.splice(Number(button.dataset.removeProfile),1);renderProfiles();schedulePlan();return;}
});
document.addEventListener('input',event=>{
  const input=event.target;if(!state.draft)return;
  if(input.dataset.ball){state.draft.ball_catching[input.dataset.ball]=Number(input.value);schedulePlan();}
  else if(input.dataset.list){
    const values=input.value.trim().split(/[\s,]+/).filter(Boolean).map(v=>Number(v)*Number(input.dataset.scale||1));
    if(!values.length||values.some(v=>!Number.isFinite(v))){planError('Enter comma-separated numeric sweep values.');return;}
    state.draft[input.dataset.list]=values;schedulePlan();
  }else if(input.dataset.value){state.draft[input.dataset.value]=Number(input.value);schedulePlan();}
  else if(input.dataset.profile!=null){const key=input.dataset.setting;state.draft.gem5_configs[Number(input.dataset.profile)][key]=input.type==='number'?Number(input.value):input.value;schedulePlan();}
});
$('sweep-name').addEventListener('input',event=>{state.draft.name=event.target.value;schedulePlan();});
document.querySelectorAll('[data-mode]').forEach(input=>input.addEventListener('change',()=>{state.draft.modes=[...document.querySelectorAll('[data-mode]:checked')].map(v=>v.dataset.mode);schedulePlan();}));
$('workload').addEventListener('change',()=>{state.draft.workload=$('workload').value;if(state.draft.workload==='synthetic'){state.draft.payload_bytes=[64];state.draft.rate_hz=[100];}renderWorkload();schedulePlan();});
$('reliability').addEventListener('change',()=>{state.draft.reliability=$('reliability').value;schedulePlan();});
$('physics-step').addEventListener('input',()=>{state.draft.physics_step_ns=Number($('physics-step').value)*1e6;schedulePlan();});
$('form-mode').onclick=()=>editorMode('form');$('json-mode').onclick=()=>editorMode('json');
$('config-json').addEventListener('input',()=>schedulePlan());
$('add-profile').onclick=()=>{const types=['kvm','timing','o3'],names=['KVM','Timing','O3'],index=Math.min(state.draft.gem5_configs.length,2);let name=names[index],suffix=2;while(state.draft.gem5_configs.some(p=>p.name===name))name=names[index]+' '+suffix++;state.draft.gem5_configs.push({...clone(state.draft.gem5_configs[0]),name,cpu_type:types[index]});renderProfiles();schedulePlan();};
$('start-sweep').onclick=()=>void startSweep();$('prepare-runtime').onclick=()=>void prepare(true);$('stage-runtime').onclick=()=>void prepare(false);
$('stop-job').onclick=async()=>{try{await api('stop',{id:state.selected});await refresh();}catch(failure){error(failure.message);}};
$('resume-job').onclick=async()=>{
  try{
    await api('resume',{id:state.selected,timeout:Number($('resume-timeout').value)});
    await refresh();state.logPinned=false;renderMonitor();void fetchLogs();
  }catch(failure){error(failure.message);}
};
$('dismiss-error').onclick=()=>{$('error-banner').hidden=true;};
$('runtime-toggle').onclick=()=>{const open=$('runtime-panel').hidden;$('runtime-panel').hidden=!open;$('runtime-toggle').setAttribute('aria-expanded',String(open));};
$('runtime-form').addEventListener('submit',async event=>{event.preventDefault();try{await api('runtime',Object.fromEntries(new FormData(event.target)));state.runtimeSignature='';await refresh();}catch(failure){error(failure.message);}});
$('export-config').onclick=()=>download(state.draft,'latency-sweep.json');
$('load-config').onclick=()=>{$('config-file').click();};
$('config-file').addEventListener('change',async()=>{try{const file=$('config-file').files[0];if(!file)return;if(file.size>1024**2)throw new Error('Configuration files must be at most 1 MiB.');const config=JSON.parse(await file.text());const plan=await api('plan',config);state.draft=plan.config;renderForm();schedulePlan();setView('configure');}catch(failure){error(failure.message);}finally{$('config-file').value='';}});
$('new-sweep').onclick=()=>{state.selected=null;state.report=null;state.reportKey='';renderHistory();setView('configure');};
$('clone-sweep').onclick=async()=>{const job=selectedJob();if(job?.config){try{state.draft=(await api('plan',job.config)).config;renderForm();schedulePlan();setView('configure');}catch(failure){error(failure.message);}}};
$('import-results').onclick=openImport;$('results-import').onclick=openImport;$('close-import').onclick=()=>{$('import-dialog').close();};
$('import-form').addEventListener('submit',async event=>{event.preventDefault();try{const job=await api('import',{path:$('import-path').value});$('import-dialog').close();await refresh();await selectJob(job.id,'results');}catch(failure){error(failure.message);}});
$('flavor').addEventListener('change',()=>{benchTheme.apply($('flavor').value,benchTheme.accent);themeControls();});
window.addEventListener('themechange',()=>{if(state.report?.runs.length)renderHeatmaps();});
for(const id of ['filter-profile','filter-body','filter-rate','filter-repeat','reference','statistic','filter-stream'])$(id).addEventListener('change',()=>{state.pair=null;renderHeatmaps();});
$('detail-case').addEventListener('change',()=>void renderDetail());
$('log-case').addEventListener('change',()=>{state.logPinned=true;void fetchLogs();});$('log-stream').addEventListener('change',()=>void fetchLogs());
$('preset').addEventListener('change',async()=>{
  const preset=$('preset').value;
  if(preset==='ball'){
    try{const response=await fetch('/experiments/ball-catching.json');if(!response.ok)throw new Error('Experiment preset is missing.');state.draft=await response.json();renderForm();schedulePlan();}catch(failure){error(failure.message);}return;
  }
  state.draft.workload='synthetic';
  Object.assign(state.draft,preset==='quick'?{payload_bytes:[64],rate_hz:[100],interval_us:[50000,20000],poll_us:[10000,5000],duration:.5,warmup:.1,drain:.2,repeats:1}:
    preset==='study'?{payload_bytes:[64,1024],rate_hz:[20,97],interval_us:[50000,20000],poll_us:[10000,5000],duration:10,warmup:1,drain:1,repeats:3}:
    {payload_bytes:[64,1024],rate_hz:[1000],interval_us:[50000,20000],poll_us:[10000,5000],duration:10,warmup:1,drain:1,repeats:3});
  renderForm();schedulePlan();
});
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(state.details.length)drawPlots();},100);});
(async()=>{
  themeControls();
  try{state.draft=await api('defaults');try{const saved=JSON.parse(localStorage.getItem('chimaera-latency-draft'));if(saved)state.draft=(await api('plan',saved)).config;}catch{}
    renderForm();await validatePlan();await refresh();
  }catch(failure){error(failure.message);}
  const poll=async()=>{await refresh();setTimeout(poll,2000);};setTimeout(poll,2000);
})();
