'use client';
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import dynamic from 'next/dynamic';
import { Activity, ArrowDownToLine, Bell, Check, ChevronRight, CirclePause, Clock3,
  FileJson, Layers3, Moon, Play, Plus, RotateCcw, Server, Settings2, Square, Sun,
  Terminal, Trash2, X } from 'lucide-react';
import { useTheme } from '@/components/theme-provider';
import type { Case, Machine, State } from '@/lib/types';

const Editor = dynamic(() => import('@/components/experiment-editor'), { ssr: false,
  loading: () => <div className="editor-loading">Loading experiment editor…</div> });
const activeStatuses = ['preparing', 'running', 'stopping', 'needs_attention'];
const busyStatuses = ['dispatching', 'running', 'collecting'];
const occupiedStatuses = ['dispatching', 'running'];

async function api(path: string, body?: unknown) {
  const response = await fetch(`/api/${path}`, body === undefined ? { cache: 'no-store' } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function time(value: number | null | undefined) {
  if (value == null) return 'Awaiting data';
  if (value < 60) return 'Less than a minute';
  if (value < 3600) return `~${Math.ceil(value / 60)} min`;
  return `~${Math.floor(value / 3600)}h ${Math.ceil(value % 3600 / 60)}m`;
}
function label(value: string) { return value.replaceAll('_', ' '); }
function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}><i />{label(value)}</span>;
}
function Dialog({ title, close, children, wide = false }: { title: string; close: () => void; children: ReactNode; wide?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className={wide ? 'dialog wide' : 'dialog'} onCancel={close} aria-label={title}>
    <div className="dialog-heading"><h2>{title}</h2><button className="icon-button" onClick={close} aria-label="Close"><X size={20} /></button></div>
    {children}
  </dialog>;
}

export default function Dashboard() {
  const { light, toggle } = useTheme();
  const [state, setState] = useState<State | null>(null);
  const [connectionError, setConnectionError] = useState('');
  const [error, setError] = useState('');
  const [selected, setSelected] = useState('');
  const [view, setView] = useState<'fleet' | 'experiments' | 'notifications'>('fleet');
  const [machine, setMachine] = useState<Machine | null>(null);
  const [editing, setEditing] = useState(false);
  const [editorConfig, setEditorConfig] = useState<Record<string, unknown> | undefined>();
  const [log, setLog] = useState<{ machine: string; task: string; title: string } | null>(null);
  const [confirmation, setConfirmation] = useState<{ title: string; description: string; action: () => void } | null>(null);
  const knownErrors = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    try {
      const next: State = await api('state');
      setState(next); setConnectionError('');
      for (const command of next.commands) if (command.status === 'failed' && !knownErrors.current.has(command.id)) {
        knownErrors.current.add(command.id); setError(command.error || 'Action failed');
      }
    } catch (error) { setConnectionError((error as Error).message); }
  }, []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { await refresh(); if (!stopped) timer = setTimeout(poll, 3000); };
    void poll(); return () => { stopped = true; clearTimeout(timer); };
  }, [refresh]);

  const command = async (kind: string, body: unknown) => {
    try { await api('command', { kind, body }); await refresh(); return true; }
    catch (error) { setError((error as Error).message); return false; }
  };
  const active = state?.experiments.find(e => activeStatuses.includes(e.status));
  const experiment = state?.experiments.find(e => e.id === selected) || active || state?.experiments.at(-1);
  const cases = experiment?.cases || [];
  const completed = cases.filter(c => c.status === 'completed').length;
  const running = cases.filter(c => occupiedStatuses.includes(c.status)).length;
  const failed = cases.filter(c => c.status === 'failed').length;
  const machines = state?.machines || [];
  const newMachine = () => setMachine({ id: crypto.randomUUID().replaceAll('-', ''), name: '', target: '',
    port: 22, repo: '', workers: 1, cpus: 1, memory: '2g', build_jobs: 2, disk: '', kernel: '' });
  const openEditor = (config?: Record<string, unknown>) => { setEditorConfig(config); setEditing(true); };
  const showLogs = (machine: string, task: string, title: string) => setLog({ machine, task, title });

  return <div className="app-shell">
    <aside className="sidebar">
      <a href="/" className="brand" aria-label="Chimaera home"><span className="brand-mark"><Layers3 size={25} /></span><span>Chimaera<small>Experiment control</small></span></a>
      <nav aria-label="Main navigation">
        <button className={view === 'fleet' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('fleet')}><Server size={18} />Machines<span>{machines.length}</span></button>
        <button className={view === 'experiments' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('experiments')}><Layers3 size={18} />Experiments<span>{state?.experiments.length || 0}</span></button>
        <button className={view === 'notifications' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('notifications')}><Bell size={18} />Notifications</button>
      </nav>
      <div className="queue-nav"><div className="section-title"><h3>Experiment queue</h3><button className="icon-button" onClick={() => openEditor()} aria-label="New experiment"><Plus size={16} /></button></div>
        {!state?.experiments.length && <p className="muted small">Create an experiment to start assigning cases.</p>}
        {state?.experiments.filter(e => activeStatuses.includes(e.status) || e.status === 'queued').map(e => <button
          key={e.id} className={`queue-item ${experiment?.id === e.id ? 'current' : ''}`} onClick={() => { setSelected(e.id); setView('fleet'); }}>
          <span className={`queue-dot ${e.status}`} /><span>{e.name}<small>{label(e.status)} · {e.cases.filter(c => c.status === 'completed').length}/{e.cases.length} cases</small></span>
        </button>)}
      </div>
      <div className="sidebar-bottom"><span className={`connection ${connectionError ? 'offline' : ''}`}><i />{connectionError ? 'Connection interrupted' : state ? 'Scheduler connected' : 'Connecting…'}</span>
        <button className="theme-button" onClick={toggle}>{light ? <Moon size={17} /> : <Sun size={17} />}{light ? 'Dark appearance' : 'Light appearance'}</button>
        <p>Ball catching / Memory contention</p>
      </div>
    </aside>

    <main>
      <header className="topbar"><span>Ball catching <ChevronRight size={14} /><strong>{view === 'fleet' ? 'Machines & workers' : view === 'experiments' ? 'Experiments' : 'Notifications'}</strong></span>
        <span className="muted small"><Clock3 size={14} />{state ? `Updated ${new Date(state.time * 1000).toLocaleTimeString()}` : 'Waiting for scheduler'}</span>
      </header>
      {connectionError && <div className="banner danger"><Activity size={18} /><span>{connectionError} Displayed data may be stale.</span></div>}
      {error && <div className="banner danger" role="alert"><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}

      {view === 'notifications' ? <NotificationSettings state={state} save={body => command('settings', body)} /> : <>
        <div className="page-heading"><div><h1>{view === 'fleet' ? 'Machines & workers' : 'Experiments'}</h1><p>{view === 'fleet' ? 'One case queue. All your available machines.' : 'Configure, queue, and revisit memory-contention runs.'}</p></div>
          <div className="actions">{view === 'fleet' && <button className="button" onClick={newMachine}><Plus size={16} />Add machine</button>}<button className="button primary" onClick={() => openEditor()}><Plus size={16} />New experiment</button></div>
        </div>

        {view === 'experiments' ? <section className="experiment-list">
          {!state?.experiments.length && <Empty title="Your first distributed experiment" description="Add machines, then open the memory-contention template to configure a run." action={() => openEditor()} actionLabel="Create experiment" />}
          {state?.experiments.slice().reverse().map(e => <button className="experiment-row" key={e.id} onClick={() => { setSelected(e.id); setView('fleet'); }}><FileJson size={23} /><span><strong>{e.name}</strong><small>{new Date(e.created * 1000).toLocaleString()} · {e.machines.length} machines</small></span><Status value={e.status} /><span>{e.cases.filter(c => c.status === 'completed').length}/{e.cases.length} cases</span><ChevronRight size={18} /></button>)}
        </section> : <>
          <section className="fleet" aria-label="Machine allocation">
            {!machines.length && <Empty title="Connect your first machine" description="Supply its SSH address and repository path. The dashboard deploys a containerized supervisor and prepares missing assets." action={newMachine} actionLabel="Add machine" />}
            {machines.map(m => {
              const assigned = cases.filter(c => occupiedStatuses.includes(c.status) && c.attempts.at(-1)?.machine === m.id);
              const preparation = experiment?.preparations[m.id];
              const stale = !m.online || Boolean(connectionError) || !m.seen || (Date.now() / 1000 - m.seen > 30);
              return <article className={`machine ${stale ? 'stale' : ''}`} key={m.id}>
                <div className="machine-heading"><span className="machine-symbol"><Server size={21} /></span><div><h2>{m.name}</h2><p>{m.target}</p></div><Status value={m.deployed ? stale ? 'offline' : 'online' : 'not_deployed'} /><button className="icon-button" aria-label={`Edit ${m.name}`} onClick={() => setMachine(m)}><Settings2 size={18} /></button></div>
                <div className="machine-allocation"><span><strong>{assigned.length}</strong> / {m.workers} workers active</span><span>{m.cpus} CPU · {m.memory} per worker</span></div>
                <div className="worker-slots">{Array.from({ length: Math.min(Math.max(m.workers, assigned.length), 32) }, (_, index) => {
                  const c = assigned[index]; const a = c?.attempts.at(-1);
                  const stats = m.snapshot?.stats.find(s => s.Name === `chimaera-${a?.id}`);
                  return <div key={index} className={`worker-slot ${c ? 'occupied' : ''}`}>
                    <div><span className="slot-mark">{c ? <Activity size={15} /> : <span />}</span><strong>{c ? c.id : 'Available'}</strong>{a && <button className="icon-button" onClick={() => showLogs(m.id, a.id, c.id)} aria-label={`Logs for ${c.id}`}><Terminal size={15} /></button>}</div>
                    {c && a ? <><p>{a.progress?.phase || label(c.status)}</p><div className="slot-data"><span title={c.eta_basis}><Clock3 size={12} />{stale ? 'Progress stale' : time(c.eta_seconds)}</span><span>{stats ? `${stats.CPUPerc} CPU` : 'CPU awaiting sample'}</span></div><small>{stats?.MemUsage || 'RAM awaiting sample'}</small>
                      <div className="progress-track"><div style={{ width: `${a.progress?.throw_percent || 0}%` }} /></div></> : <p>Next case assigned automatically</p>}
                  </div>;
                })}</div>
                {m.workers > 32 && <p className="muted small">Showing 32 of {m.workers} worker slots.</p>}
                {preparation && preparation.status !== 'ready' && <div className="preparation"><span>{preparation.phase || label(preparation.status)}</span>{preparation.task && <button className="text-button" onClick={() => showLogs(m.id, preparation.task!, `${m.name} preparation`)}>Logs</button>}{['failed', 'stopped'].includes(preparation.status) && <button className="text-button" onClick={() => command('retry_prepare', { id: experiment!.id, machine: m.id })}>Retry preparation</button>}</div>}
                {m.error && <p className="machine-error">{m.error}</p>}
                <footer className="machine-footer"><span>{stale ? m.seen ? `Last seen ${new Date(m.seen * 1000).toLocaleTimeString()}` : 'Awaiting connection' : 'Ready for dispatch'}</span><button className="text-button" onClick={() => command('deploy', { id: m.id })}>{m.deployed ? 'Redeploy supervisor' : 'Deploy supervisor'}</button></footer>
              </article>;
            })}
          </section>

          {experiment ? <section className="run-panel">
            <div className="run-heading"><div><div className="heading-line"><h2>{experiment.name}</h2><Status value={experiment.status} /></div><p>Memory contention · {experiment.machines.length} machines · Frozen calibration</p></div>
              <div className="actions"><button className="button compact" onClick={() => openEditor(experiment.config)}><FileJson size={15} />Clone config</button>
                {activeStatuses.includes(experiment.status) && experiment.status !== 'stopping' && <button className="button compact danger-button" onClick={() => setConfirmation({ title: 'Stop this experiment?', description: 'Terminate active cases immediately on reachable machines. Completed results are preserved. Unreachable machines show Stop pending.', action: () => { void command('stop', { id: experiment.id }); } })}><Square size={14} />Stop</button>}
                {experiment.status === 'queued' && <button className="button compact" onClick={() => command('stop', { id: experiment.id })}><CirclePause size={14} />Remove from queue</button>}
                {experiment.status === 'stopped' && <button className="button compact primary" onClick={() => command('resume', { id: experiment.id })}><Play size={14} />Resume</button>}
              </div>
            </div>
            <div className="run-summary"><div className="completion"><strong>{completed}<span> / {cases.length}</span></strong><span>Cases complete</span><div className="segmented-progress" aria-label={`${completed} of ${cases.length} cases complete`}>{cases.map(c => <span className={`segment ${c.status}`} key={c.id} title={`${c.id}: ${label(c.status)}`} />)}</div></div>
              <div><strong>{running}</strong><span>Active</span></div><div><strong className={failed ? 'danger-text' : ''}>{failed}</strong><span>Failed</span></div><div className="eta" title={experiment.eta_basis}><span><Clock3 size={15} />Estimated remaining</span><strong>{time(experiment.eta_seconds)}</strong><small>{experiment.eta_basis}</small></div></div>
            {experiment.status === 'needs_attention' && <div className="banner warning"><span>The queue is waiting for your decision. Retry failures or accept partial results.</span><div className="actions"><button className="button compact" onClick={() => command('retry', { id: experiment.id })}><RotateCcw size={14} />Retry failed cases</button><button className="button compact" onClick={() => command('accept_partial', { id: experiment.id })}>Accept partial results</button></div></div>}
            {experiment.status === 'stopping' && <div className="banner warning">Stop pending on {experiment.stop_pending?.map(id => machines.find(m => m.id === id)?.name || id).join(', ') || 'active machines'}.</div>}
            <div className="case-table-wrap"><table className="case-table"><thead><tr><th>Case</th><th>Traffic pattern</th><th>Pressure</th><th>Machine</th><th>Status</th><th>Estimated remaining</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{cases.map(c => {
              const a = c.attempts.at(-1);
              return <tr key={c.id}><td><strong>{c.id}</strong>{a?.progress?.outcome && <small>{a.progress.outcome.toLowerCase()}</small>}</td><td>{c.point.pattern || 'Baseline'}</td><td>{Math.round((c.point.pressure || 0) * 100)}%</td><td>{machines.find(m => m.id === a?.machine)?.name || 'Unassigned'}</td><td><Status value={c.status} />{a?.oom && <small className="danger-text">Memory limit exceeded</small>}</td><td title={c.eta_basis}>{busyStatuses.includes(c.status) ? time(c.eta_seconds) : '—'}</td><td><div className="actions">{a && <button className="icon-button" onClick={() => showLogs(a.machine, a.id, c.id)} aria-label={`Logs for ${c.id}`}><Terminal size={16} /></button>}{c.status === 'failed' && ['running', 'needs_attention'].includes(experiment.status) && <button className="icon-button" onClick={() => command('retry', { id: experiment.id, case: c.id })} aria-label={`Retry ${c.id}`}><RotateCcw size={16} /></button>}</div></td></tr>;
            })}</tbody></table></div>
            <div className="result-actions"><div className="actions"><a className="button compact" href={`/api/download/${experiment.id}`} aria-disabled={experiment.results_deleted} onClick={e => { if (experiment.results_deleted) e.preventDefault(); }}><ArrowDownToLine size={15} />Download collected results</a><a className="button compact" href={`/api/report/${experiment.id}`} target="_blank" rel="noreferrer">Open comparison report</a></div>
              {!activeStatuses.includes(experiment.status) && experiment.status !== 'queued' && <div className="actions"><button className="text-button" onClick={() => setConfirmation({ title: 'Clean remote copies?', description: 'Remove this experiment’s remote files and stopped containers after verifying every attempt was collected.', action: () => { void command('cleanup', { id: experiment.id }); } })}>Clean remote copies</button><button className="icon-button danger-text" aria-label="Delete collected results" onClick={() => setConfirmation({ title: 'Delete collected results?', description: 'Delete this experiment’s result files from the webserver. Run history remains. Clean remote copies first if you also want to remove them.', action: () => { void command('delete', { id: experiment.id }); } })}><Trash2 size={16} /></button></div>}
            </div>
          </section> : machines.length > 0 && <Empty title="Machines connected. Next, an experiment." description="Open the current memory-contention template, choose machines, and queue the run." action={() => openEditor()} actionLabel="Create experiment" />}
          <section className="activity"><div className="section-title"><h2>Activity</h2><span className="muted small">Latest scheduler events</span></div>{!state?.events.length && <p className="muted">Experiment and machine events will appear here.</p>}{state?.events.slice(-12).reverse().map(item => <div className={`activity-row ${item.level}`} key={item.id}><span className="activity-dot" /><p>{item.message}</p><time>{new Date(item.time * 1000).toLocaleTimeString()}</time></div>)}</section>
        </>}
      </>}
    </main>
    {machine && <MachineDialog machine={machine} close={() => setMachine(null)} save={async value => { if (await command('machine', value)) setMachine(null); }} />}
    {editing && <ExperimentDialog initial={editorConfig} machines={machines} close={() => setEditing(false)} save={async body => { if (await command('experiment', body)) { setEditing(false); setView('fleet'); setSelected(''); } }} />}
    {log && <LogDialog {...log} close={() => setLog(null)} />}
    {confirmation && <Dialog title={confirmation.title} close={() => setConfirmation(null)}><p className="dialog-description">{confirmation.description}</p><div className="dialog-actions"><button className="button" onClick={() => setConfirmation(null)}>Cancel</button><button className="button danger-button" onClick={() => { confirmation.action(); setConfirmation(null); }}>Confirm</button></div></Dialog>}
  </div>;
}

function Empty({ title, description, action, actionLabel }: { title: string; description: string; action: () => void; actionLabel: string }) {
  return <div className="empty"><span className="empty-symbol"><Layers3 size={30} /></span><h2>{title}</h2><p>{description}</p><button className="button primary" onClick={action}><Plus size={16} />{actionLabel}</button></div>;
}

function MachineDialog({ machine, close, save }: { machine: Machine; close: () => void; save: (machine: Machine) => Promise<void> }) {
  const [value, setValue] = useState(machine);
  const [saving, setSaving] = useState(false);
  const field = (key: keyof Machine, name: string, type = 'text', required = true) => <label>{name}<input required={required} type={type} value={String(value[key] ?? '')} min={type === 'number' ? key === 'cpus' ? 0.1 : 1 : undefined} step={key === 'cpus' ? 0.1 : 1} onChange={e => setValue({ ...value, [key]: type === 'number' ? Number(e.target.value) : e.target.value })} /></label>;
  return <Dialog title={machine.name ? `Configure ${machine.name}` : 'Add machine'} close={close}>
    <form onSubmit={async (e: FormEvent) => { e.preventDefault(); setSaving(true); await save(value); setSaving(false); }}>
      <div className="form-grid">{field('name', 'Machine name')}{field('target', 'SSH user@hostname')}{field('port', 'SSH port', 'number')}{field('repo', 'Repository path')}</div>
      <h3>Worker allocation</h3><p className="muted small">Changes apply to new cases. Active cases keep their existing limits.</p>
      <div className="form-grid thirds">{field('workers', 'Worker count', 'number')}{field('cpus', 'CPUs per worker', 'number')}{field('memory', 'RAM per worker')}</div>
      <details><summary>Build and guest assets</summary><div className="form-grid">{field('build_jobs', 'Build parallelism', 'number')}{field('disk', 'Existing guest disk (optional)', 'text', false)}{field('kernel', 'Existing kernel (optional)', 'text', false)}</div><p className="muted small">Missing guest assets are prepared on the calibration machine and distributed. Leave paths empty to build a guest and download the pinned kernel.</p></details>
      <div className="dialog-actions"><button type="button" className="button" onClick={close}>Cancel</button><button disabled={saving} className="button primary">{saving ? 'Saving…' : 'Save machine'}</button></div>
    </form>
  </Dialog>;
}

function ExperimentDialog({ initial, machines, close, save }: { initial?: Record<string, unknown>; machines: Machine[]; close: () => void; save: (body: unknown) => Promise<void> }) {
  const [config, setConfig] = useState(initial ? JSON.stringify(initial, null, 2) : '');
  const [name, setName] = useState('Memory contention');
  const [selected, setSelected] = useState(machines.map(m => m.id));
  const [points, setPoints] = useState<Case['point'][] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!initial) api('defaults').then(result => setConfig(JSON.stringify(result.config, null, 2))).catch(e => setError(e.message)); }, [initial]);
  const validate = async () => {
    setBusy(true); setError('');
    try { const result = await api('validate', { config: JSON.parse(config) }); setPoints(result.points); return result.config; }
    catch (error) { setError((error as Error).message); setPoints(null); return null; }
    finally { setBusy(false); }
  };
  return <Dialog title="Configure experiment" close={close} wide>
    <div className="editor-toolbar"><label>Experiment name<input value={name} onChange={e => setName(e.target.value)} /></label><div className="actions"><label className="button compact file-button">Import JSON<input type="file" accept=".json,application/json" onChange={async e => { const file = e.target.files?.[0]; if (file) { setConfig(await file.text()); setPoints(null); } }} /></label><button className="button compact" onClick={() => { const url = URL.createObjectURL(new Blob([config], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = 'experiment.json'; link.click(); URL.revokeObjectURL(url); }}>Export JSON</button></div></div>
    <div className="editor-layout"><div className="editor-pane"><div className="editor-tab"><FileJson size={15} />experiment.json</div><div className="editor-area"><Editor value={config} onChange={value => { setConfig(value); setPoints(null); }} /></div></div>
      <aside className="experiment-options"><h3>Run on these machines</h3><p className="muted small">Cases are assigned dynamically. The first selected machine freezes calibration and guest assets.</p>
        {!machines.length && <p>Add a machine before queueing.</p>}{machines.map(m => <label className="machine-choice" key={m.id}><input type="checkbox" checked={selected.includes(m.id)} onChange={e => setSelected(e.target.checked ? [...selected, m.id] : selected.filter(id => id !== m.id))} /><span>{m.name}<small>{m.workers} workers · {m.cpus} CPU · {m.memory}</small></span></label>)}
        <div className="preview"><h3>Expanded cases</h3>{points ? <><p className="preview-count">{points.length}<span>cases</span></p><div className="preview-list">{points.map((p, i) => <div key={i}><span>{String(p.id)}</span><span>{String(p.pattern || 'baseline')} / {Math.round(Number(p.pressure || 0) * 100)}%</span></div>)}</div></> : <p className="muted small">Validate the configuration to preview the case queue.</p>}</div>
      </aside></div>
    {error && <p className="editor-error" role="alert">{error}</p>}
    <div className="dialog-actions"><span className="muted small">One experiment runs at a time. Failures wait for your decision.</span><button className="button" disabled={busy} onClick={validate}><Check size={16} />Validate</button><button className="button primary" disabled={busy || !name.trim() || !selected.length} onClick={async () => { const validated = await validate(); if (validated) { setBusy(true); await save({ name, config: validated, machines: selected }); setBusy(false); } }}><Play size={15} />Queue experiment</button></div>
  </Dialog>;
}

function LogDialog({ machine, task, title, close }: { machine: string; task: string; title: string; close: () => void }) {
  const [log, setLog] = useState('Connecting to worker…');
  const [error, setError] = useState('');
  const [stream, setStream] = useState('worker');
  useEffect(() => {
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const result = await api(`logs/${machine}/${task}/${stream}`); if (!stopped) { setLog(result.log || 'No output yet.'); setError(''); } }
      catch (error) { if (!stopped) setError((error as Error).message); }
      if (!stopped) timer = setTimeout(poll, 3000);
    }; void poll(); return () => { stopped = true; clearTimeout(timer); };
  }, [machine, task, stream]);
  return <Dialog title={`${title} / Logs`} close={close} wide><div className="log-caption actions"><label>Log stream<select value={stream} onChange={e => { setStream(e.target.value); setLog('Loading log…'); }}><option value="worker">Worker output</option><option value="launch">ROS / Gazebo launch</option><option value="guest">Guest serial</option><option value="gem5">gem5 statistics</option></select></label><span>Latest output. Refreshes every three seconds. Collected logs remain available offline.</span></div>{error && <p className="editor-error" role="alert">{error}</p>}<pre className="log-output" tabIndex={0}>{log}</pre></Dialog>;
}

function NotificationSettings({ state, save }: { state: State | null; save: (body: unknown) => Promise<boolean> }) {
  const [enabled, setEnabled] = useState(true);
  const [minutes, setMinutes] = useState(60);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (state) { setEnabled(state.settings.progress_enabled); setMinutes(state.settings.progress_interval / 60); } }, [state?.settings.progress_enabled, state?.settings.progress_interval]);
  return <section className="notification-settings"><h1>Notifications</h1><p>Follow experiment progress in Discord.</p><div className="settings-section"><h2>Discord webhook</h2><Status value={state?.notifications_configured ? 'configured' : 'not_configured'} /><p className="muted">Set DISCORD_WEBHOOK_URL in the Compose environment. The webhook stays on the server.</p><ul><li>Case started</li><li>Case completed or failed</li><li>Experiment completed or stopped</li><li>Machine connectivity and preparation failures</li></ul></div><form className="settings-section" onSubmit={async e => { e.preventDefault(); setSaved(await save({ progress_enabled: enabled, progress_interval: Math.round(minutes * 60) })); }}><h2>Progress & ETA updates</h2><label className="checkbox-label"><input type="checkbox" checked={enabled} onChange={e => { setEnabled(e.target.checked); setSaved(false); }} />Send periodic updates</label><label>Interval in minutes<input type="number" min={1} step={1} value={minutes} onChange={e => { setMinutes(Number(e.target.value)); setSaved(false); }} /></label><p className="muted small">Applies to the active experiment without restarting workers.</p><button className="button primary">{saved ? <><Check size={16} />Settings queued</> : 'Save notification settings'}</button></form></section>;
}
