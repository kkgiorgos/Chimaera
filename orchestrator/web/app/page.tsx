'use client';
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import dynamic from 'next/dynamic';
import { Activity, ArrowDownToLine, Bell, Check, ChevronRight, CirclePause, Clock3,
  FileJson, HardDrive, Layers3, Moon, Play, Plus, RotateCcw, Server, Settings2, Square, Sun,
  Terminal, Trash2, X } from 'lucide-react';
import Storage from '@/components/storage';
import { useTheme } from '@/components/theme-provider';
import type { Attempt, Case, Machine, State } from '@/lib/types';

const Editor = dynamic(() => import('@/components/experiment-editor'), { ssr: false,
  loading: () => <div className="editor-loading">Loading experiment editor…</div> });
const activeStatuses = ['preparing', 'running', 'stopping', 'needs_attention'];
const busyStatuses = ['dispatching', 'running', 'stopping', 'collecting'];
const occupiedStatuses = ['dispatching', 'running', 'stopping'];

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
function bytes(value: number) {
  return value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GiB` : `${(value / 1024 ** 2).toFixed(1)} MiB`;
}
function label(value: string) { return value.replaceAll('_', ' '); }
function SimulationSpeed({ progress, stale = false }: { progress?: Attempt['progress']; stale?: boolean }) {
  const speed = progress?.average_speed;
  const age = progress?.last_update_age_seconds;
  const sampled = speed != null && Number.isFinite(speed) && speed >= 0;
  const title = sampled
    ? `Average simulated seconds per wall-clock second at the latest timing sample${age != null ? `; sample recorded ${Math.floor(age)} seconds ago` : ''}`
    : 'Simulation speed becomes available after the first timing sample';
  return <span title={title}>{sampled ? `${speed.toPrecision(3)}× real time${stale ? ' (last sample)' : ''}` : 'Awaiting sample'}</span>;
}
function PreparationProgress({ progress, stale }: { progress?: Attempt['progress']; stale: boolean }) {
  if (!progress) return null;
  const elapsed = progress.elapsed_seconds;
  return <div className="preparation-progress" role="status">
    <small>{progress.checkpoint_current != null && `Checkpoint ${progress.checkpoint_current}/${progress.checkpoint_total} · `}
      {elapsed != null && `Elapsed ${Math.floor(elapsed / 3600)}h ${Math.floor(elapsed % 3600 / 60)}m ${Math.floor(elapsed % 60)}s`}</small>
    {progress.simulated_seconds != null && <small>{progress.simulated_seconds.toFixed(4)}s simulated · {progress.completed_exchanges} exchanges · <SimulationSpeed progress={progress} stale={stale || (progress.last_update_age_seconds ?? 0) > 30} /></small>}
    {progress.readiness && <small>Arm: {progress.readiness.arm ? 'ready' : 'waiting'} · Fresh camera: {progress.readiness.perception ? 'ready' : 'waiting'}
      {progress.readiness.arm && progress.readiness.perception && (progress.readiness.warmup_required ?? 0) > 0 && ` · Warmup ${(progress.readiness.warmup_elapsed ?? 0).toFixed(3)}/${progress.readiness.warmup_required!.toFixed(3)}s`}</small>}
  </div>;
}
function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}><i />{label(value)}</span>;
}
function Dialog({ title, close, children, wide = false }: { title: string; close: () => void; children: ReactNode; wide?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (dialog.current && !dialog.current.open) dialog.current.showModal(); }, []);
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
  const [view, setView] = useState<'fleet' | 'experiments' | 'notifications' | 'storage'>('fleet');
  const [machine, setMachine] = useState<Machine | null>(null);
  const [editing, setEditing] = useState(false);
  const [editorConfig, setEditorConfig] = useState<Record<string, unknown> | undefined>();
  const [log, setLog] = useState<{ machine: string; task: string; title: string } | null>(null);
  const [confirmation, setConfirmation] = useState<{ title: string; description: string; actionLabel?: string; action: () => void } | null>(null);
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
  const experimentBlocked = !state || state.experiments.some(e => !['completed', 'partial', 'deleted'].includes(e.status) || !e.remote_cleaned);
  const experiment = state?.experiments.find(e => e.id === selected) || active || state?.experiments.at(-1);
  const cases = experiment?.cases || [];
  const completed = cases.filter(c => c.status === 'completed').length;
  const running = cases.filter(c => occupiedStatuses.includes(c.status)).length;
  const failed = cases.filter(c => c.status === 'failed').length;
  const stopped = cases.filter(c => c.status === 'stopped').length;
  const machines = state?.machines || [];
  const newMachine = () => setMachine({ id: '', name: '', target: '',
    port: 22, repo: '', workers: 1, cpus: 1, memory: '2g', build_jobs: 2, disk: '', kernel: '' });
  const openEditor = (config?: Record<string, unknown>) => { setEditorConfig(config); setEditing(true); };
  const showLogs = (machine: string, task: string, title: string) => setLog({ machine, task, title });
  const stopCase = (c: Case) => {
    const attempt = c.attempts.at(-1);
    if (!experiment || !attempt) return;
    setConfirmation({ title: `Stop ${c.id}?`, description: 'Terminate this worker and keep its logs. Other cases continue. Retrying this case starts a fresh attempt from the beginning.',
      action: () => { void command('stop_case', { id: experiment.id, case: c.id, attempt: attempt.id }); } });
  };
  const collectionErrors = [
    ...cases.flatMap(c => c.attempts.filter(a => a.collection_status === 'failed').map(a => `${c.id}: ${a.collection_error}`)),
    ...Object.entries(experiment?.preparations || {}).filter(([, p]) => p.collection_status === 'failed').map(([id, p]) => `${machines.find(m => m.id === id)?.name || id} preparation: ${p.collection_error}`),
  ];
  const canDelete = experiment && !experiment.results_deleted && experiment.status !== 'stopping'
    && !cases.some(c => occupiedStatuses.includes(c.status))
    && (experiment.status === 'stopped' || !Object.values(experiment.preparations).some(p => ['preparing', 'running', 'accepted', 'copying_sdk'].includes(p.status)));

  return <div className="app-shell">
    <aside className="sidebar">
      <a href="/" className="brand" aria-label="Chimaera home"><span className="brand-mark"><Layers3 size={25} /></span><span>Chimaera<small>Experiment control</small></span></a>
      <nav aria-label="Main navigation">
        <button className={view === 'fleet' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('fleet')}><Server size={18} />Machines<span>{machines.length}</span></button>
        <button className={view === 'experiments' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('experiments')}><Layers3 size={18} />Experiments<span>{state?.experiments.length || 0}</span></button>
        <button className={view === 'notifications' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('notifications')}><Bell size={18} />Notifications</button>
        <button className={view === 'storage' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('storage')}><HardDrive size={18} />Storage</button>
      </nav>
      <div className="queue-nav"><div className="section-title"><h3>Current experiment</h3><button className="icon-button" disabled={experimentBlocked} onClick={() => openEditor()} aria-label="New experiment"><Plus size={16} /></button></div>
        {!state?.experiments.length && <p className="muted small">Create an experiment to start assigning cases.</p>}
        {state?.experiments.filter(e => !['completed', 'partial', 'deleted'].includes(e.status) || !e.remote_cleaned).map(e => <button
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
      <header className="topbar"><span>Ball catching <ChevronRight size={14} /><strong>{view === 'fleet' ? 'Machines & workers' : view === 'experiments' ? 'Experiments' : view === 'storage' ? 'Storage' : 'Notifications'}</strong></span>
        <span className="muted small"><Clock3 size={14} />{state ? `Updated ${new Date(state.time * 1000).toLocaleTimeString()}` : 'Waiting for scheduler'}</span>
      </header>
      {connectionError && <div className="banner danger"><Activity size={18} /><span>{connectionError} Displayed data may be stale.</span></div>}
      {error && <div className="banner danger" role="alert"><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}

      {view === 'storage' ? <Storage state={state} command={command} confirm={setConfirmation} /> : view === 'notifications' ? <NotificationSettings state={state} save={body => command('settings', body)} /> : <>
        <div className="page-heading"><div><h1>{view === 'fleet' ? 'Machines & workers' : 'Experiments'}</h1><p>{view === 'fleet' ? 'One case queue. All your available machines.' : 'Configure, queue, and revisit memory-contention runs.'}</p></div>
          <div className="actions">{view === 'fleet' && <button className="button" onClick={newMachine}><Plus size={16} />Add machine</button>}<button className="button primary" disabled={experimentBlocked} onClick={() => openEditor()}><Plus size={16} />New experiment</button></div>
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
                    <div><span className="slot-mark">{c ? <Activity size={15} /> : <span />}</span><strong>{c ? c.id : 'Available'}</strong>{a && <button className="icon-button" onClick={() => showLogs(m.id, a.id, c.id)} aria-label={`Logs for ${c.id}`}><Terminal size={15} /></button>}{c && ['dispatching', 'running'].includes(c.status) && experiment?.status !== 'stopping' && <button className="icon-button danger-text" onClick={() => stopCase(c)} aria-label={`Stop ${c.id}`} title={`Stop ${c.id}`}><Square size={15} /></button>}</div>
                    {c && a ? <><p>{c.status === 'stopping' ? 'Stop pending' : a.progress?.phase || label(c.status)}</p><div className="slot-data"><span title={c.eta_basis}><Clock3 size={12} />{stale ? 'Progress stale' : time(c.eta_seconds)}</span><span>{stats ? `${stats.CPUPerc} CPU` : 'CPU awaiting sample'}</span></div><small>{stats?.MemUsage || 'RAM awaiting sample'}</small>
                      <div className="progress-track"><div style={{ width: `${a.progress?.throw_percent || 0}%` }} /></div></> : <p>Next case assigned automatically</p>}
                  </div>;
                })}</div>
                {m.workers > 32 && <p className="muted small">Showing 32 of {m.workers} worker slots.</p>}
                {preparation && preparation.status !== 'ready' && <div className="preparation"><span>{preparation.phase || label(preparation.status)}</span><PreparationProgress progress={preparation.progress} stale={!m.online || Boolean(connectionError) || !m.seen || Date.now() / 1000 - m.seen > 30 || !['preparing', 'running', 'accepted'].includes(preparation.status)} />{preparation.transfer && <small role="status">{preparation.transfer.image_kind} {preparation.transfer.image_index}/{preparation.transfer.image_count} · {preparation.transfer.phase}{preparation.transfer.image_kind !== 'Readiness assets' && <> · {bytes(preparation.transfer.bytes_sent)} compressed · {bytes(preparation.transfer.bytes_per_second)}/s · {Math.floor(preparation.transfer.elapsed_seconds)}s</>} · <span title={preparation.transfer.eta_basis}>{preparation.transfer.phase === 'Streaming' ? `Image ETA: ${time(preparation.transfer.eta_seconds)}` : preparation.transfer.phase === 'Checking destination cache' ? 'Estimating ETA' : preparation.transfer.phase}</span> · from {preparation.transfer.source}{preparation.transfer.image_kind !== 'Readiness assets' && Date.now() / 1000 - preparation.transfer.updated > 10 && preparation.status === 'copying_sdk' ? ' · awaiting update' : ''}</small>}{preparation.task && <button className="text-button" onClick={() => showLogs(m.id, preparation.task!, `${m.name} preparation`)}>Logs</button>}{['failed', 'stopped'].includes(preparation.status) && <button className="text-button" onClick={() => command('retry_prepare', { id: experiment!.id, machine: m.id })}>Retry preparation</button>}</div>}
                {preparation?.error && <p className="machine-error" role="alert">{preparation.error}</p>}
                {m.error && <p className="machine-error">{m.error}</p>}
                {m.cleanup_error && <p className="machine-error" role="alert">Asset cleanup: {m.cleanup_error}</p>}
                <div className="preparation"><button className="text-button" disabled={!m.deployed || m.cleaning || state?.experiments.some(e => activeStatuses.includes(e.status) && e.machines.includes(m.id))} onClick={() => setConfirmation({ title: `Clean unused assets on ${m.name}?`, description: 'Remove unused cached SDK and application images and generated guest disks. The current reusable base and application, unfinished experiments, container dependencies and configured guest assets are preserved.', action: () => { void command('clean_assets', { id: m.id }); } })}>{m.cleaning ? 'Cleaning assets…' : 'Clean unused assets'}</button>{m.cleanup_result && <span>Last cleanup: {m.cleanup_result.images} images · {m.cleanup_result.disks} disks · {(m.cleanup_result.disk_bytes / 1024 ** 3).toFixed(1)} GiB disk space freed</span>}</div>
                <footer className="machine-footer"><span>{stale ? m.seen ? `Last seen ${new Date(m.seen * 1000).toLocaleTimeString()}` : 'Awaiting connection' : 'Ready for dispatch'}</span><button className="text-button" onClick={() => command('deploy', { id: m.id })}>{m.deployed ? 'Redeploy supervisor' : 'Deploy supervisor'}</button></footer>
              </article>;
            })}
          </section>

          {experiment ? <section className="run-panel">
            <div className="run-heading"><div><div className="heading-line"><h2>{experiment.name}</h2><Status value={experiment.status} /></div><p>Memory contention · {experiment.machines.length} machines · Image preparation & calibration: {machines.find(m => m.id === (experiment.calibration_machine || experiment.machines[0]))?.name || 'Unknown machine'}</p></div>
              <div className="actions"><button className="button compact" disabled={experimentBlocked} onClick={() => openEditor(experiment.config)}><FileJson size={15} />Clone config</button>
                {activeStatuses.includes(experiment.status) && experiment.status !== 'stopping' && <button className="button compact danger-button" onClick={() => setConfirmation({ title: 'Stop this experiment?', description: 'Terminate active cases immediately on reachable machines. Completed results are preserved. Unreachable machines show Stop pending.', action: () => { void command('stop', { id: experiment.id }); } })}><Square size={14} />Stop</button>}
                {experiment.status === 'queued' && <button className="button compact" onClick={() => command('stop', { id: experiment.id })}><CirclePause size={14} />Cancel start</button>}
                {experiment.status === 'stopped' && !experiment.remote_cleaned && <button className="button compact primary" onClick={() => command('resume', { id: experiment.id })}><Play size={14} />Resume</button>}
              </div>
            </div>
            <div className="run-summary"><div className="completion"><strong>{completed}<span> / {cases.length}</span></strong><span>Cases complete</span><div className="segmented-progress" aria-label={`${completed} of ${cases.length} cases complete`}>{cases.map(c => <span className={`segment ${c.status}`} key={c.id} title={`${c.id}: ${label(c.status)}`} />)}</div></div>
              <div><strong>{running}</strong><span>Active</span></div><div><strong className={failed ? 'danger-text' : ''}>{failed}</strong><span>Failed</span></div>{stopped > 0 && <div><strong>{stopped}</strong><span>Stopped</span></div>}<div className="eta" title={experiment.eta_basis}><span><Clock3 size={15} />Estimated remaining</span><strong>{time(experiment.eta_seconds)}</strong><small>{experiment.eta_basis}</small></div></div>
            {experiment.status === 'needs_attention' && <div className="banner warning"><span>The experiment is waiting for your decision. Retry failed or individually stopped cases, accept partial results, or delete this experiment.</span><div className="actions"><button className="button compact" disabled={!failed} onClick={() => command('retry', { id: experiment.id })}><RotateCcw size={14} />Retry failed cases</button><button className="button compact" onClick={() => command('accept_partial', { id: experiment.id })}>Finalize results</button></div></div>}
            {collectionErrors.length > 0 && !experiment.results_deleted && <div className="banner warning collection-errors" role="alert"><div><strong>Result collection failed</strong><p>Worker execution is tracked separately. Retry downloading output, or delete the experiment without waiting for collection.</p><details><summary>Show {collectionErrors.length} collection errors</summary>{collectionErrors.map((message, i) => <p key={i}>{message}</p>)}</details></div><button className="button compact" onClick={() => command('retry_collection', { id: experiment.id })}><RotateCcw size={14} />Retry collection</button></div>}
            {experiment.status === 'stopped' && !experiment.remote_cleaned && <div className="banner warning"><span>Resume this experiment, or finalize it to keep collected results and release its working files.</span><button className="button compact" onClick={() => command('accept_partial', { id: experiment.id })}>Finalize results</button></div>}
            {['completed', 'partial', 'deleted'].includes(experiment.status) && <div className="banner" role="status">{experiment.remote_cleaned ? 'Working files released. Collected results remain available.' : 'Collecting remaining results and releasing working files before the next experiment.'}{Object.entries(experiment.cleanup_errors || {}).map(([id, message]) => <p key={id}>{machines.find(m => m.id === id)?.name || id}: {message}. Cleanup retries automatically.</p>)}</div>}
            {experiment.status === 'stopping' && <div className="banner warning">Stop pending on {experiment.stop_pending?.map(id => machines.find(m => m.id === id)?.name || id).join(', ') || 'active machines'}.</div>}
            <div className="case-table-wrap"><table className="case-table"><thead><tr><th>Case</th><th>Traffic pattern</th><th>Pressure</th><th>Machine</th><th>Status</th><th>Simulation speed</th><th>Estimated remaining</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{cases.map(c => {
              const a = c.attempts.at(-1);
              const machine = machines.find(m => m.id === a?.machine);
              const speedStale = occupiedStatuses.includes(c.status) && (!machine?.online || Boolean(connectionError) || !machine.seen || Date.now() / 1000 - machine.seen > 30 || (a?.progress?.last_update_age_seconds ?? 0) > 30);
              return <tr key={c.id}><td><strong>{c.id}</strong>{a?.progress?.outcome && <small>{a.progress.outcome.toLowerCase()}</small>}</td><td>{c.point.pattern || 'Baseline'}</td><td>{Math.round((c.point.pressure || 0) * 100)}%</td><td>{machines.find(m => m.id === a?.machine)?.name || 'Unassigned'}</td><td><Status value={c.status} />{c.stop_requested && <small title={c.stop_error}>Stop pending{machine?.online ? '' : ' · machine offline'}</small>}{a?.oom && <small className="danger-text">Memory limit exceeded</small>}{a?.memory_warning && <small className="warning-text" title={a.memory_warning}>RAM budget exceeded · peak {((a.memory_peak_bytes || 0) / 1024 ** 3).toFixed(2)} GiB</small>}</td><td><SimulationSpeed progress={a?.progress} stale={speedStale} /></td><td title={c.eta_basis}>{busyStatuses.includes(c.status) ? time(c.eta_seconds) : '—'}</td><td><div className="actions">{a && <button className="icon-button" onClick={() => showLogs(a.machine, a.id, c.id)} aria-label={`Logs for ${c.id}`}><Terminal size={16} /></button>}{['dispatching', 'running'].includes(c.status) && experiment.status !== 'stopping' && <button className="icon-button danger-text" onClick={() => stopCase(c)} aria-label={`Stop ${c.id}`} title={`Stop ${c.id}`}><Square size={16} /></button>}{['failed', 'stopped'].includes(c.status) && ['running', 'needs_attention'].includes(experiment.status) && <button className="icon-button" onClick={() => command('retry', { id: experiment.id, case: c.id })} aria-label={`Retry ${c.id}`}><RotateCcw size={16} /></button>}</div></td></tr>;
            })}</tbody></table></div>
            <div className="result-actions"><div className="actions"><a className="button compact" href={`/api/download/${experiment.id}`} aria-disabled={experiment.results_deleted} onClick={e => { if (experiment.results_deleted) e.preventDefault(); }}><ArrowDownToLine size={15} />Download collected results</a><a className="button compact" href={`/api/report/${experiment.id}`} target="_blank" rel="noreferrer">Open comparison report</a></div>
              <div className="actions">{!experiment.remote_cleaned && !activeStatuses.includes(experiment.status) && experiment.status !== 'queued' && <button className="text-button" onClick={() => setConfirmation({ title: 'Clean remote copies?', description: 'Remove this experiment’s remote files and stopped containers. Any uncollected output will be permanently discarded; downloaded results stay on the webserver.', action: () => { void command('cleanup', { id: experiment.id, discard_uncollected: true }); } })}>Clean remote copies</button>}<button className="icon-button danger-text" disabled={!canDelete} title={canDelete ? 'Delete experiment' : 'Stop active workers before deleting'} aria-label="Delete experiment" onClick={() => setConfirmation({ title: 'Delete experiment?', description: 'Cancel result collection and delete this experiment’s files from the webserver. History remains marked deleted. Remote working files are released automatically after outstanding operations settle.', action: () => { void command('delete', { id: experiment.id }); } })}><Trash2 size={16} /></button></div>
            </div>
          </section> : machines.length > 0 && <Empty title="Machines connected. Next, an experiment." description="Open the current memory-contention template, choose machines, and queue the run." action={() => openEditor()} actionLabel="Create experiment" />}
          <section className="activity"><div className="section-title"><h2>Activity</h2><span className="muted small">Latest scheduler events</span></div>{!state?.events.length && <p className="muted">Experiment and machine events will appear here.</p>}{state?.events.slice(-12).reverse().map(item => <div className={`activity-row ${item.level}`} key={item.id}><span className="activity-dot" /><p>{item.message}</p><time>{new Date(item.time * 1000).toLocaleTimeString()}</time></div>)}</section>
        </>}
      </>}
    </main>
    {machine && <MachineDialog key={machine.id || 'new'} machine={machine} close={() => setMachine(null)} save={async value => { if (await command('machine', value)) setMachine(null); }} />}
    {editing && <ExperimentDialog initial={editorConfig} machines={machines} close={() => setEditing(false)} save={async body => { if (await command('experiment', body)) { setEditing(false); setView('fleet'); setSelected(''); } }} />}
    {log && <LogDialog {...log} close={() => setLog(null)} />}
    {confirmation && <Dialog title={confirmation.title} close={() => setConfirmation(null)}><p className="dialog-description">{confirmation.description}</p><div className="dialog-actions"><button className="button" onClick={() => setConfirmation(null)}>Cancel</button><button className="button danger-button" onClick={() => { confirmation.action(); setConfirmation(null); }}>{confirmation.actionLabel || 'Confirm'}</button></div></Dialog>}
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
      <div className="form-grid thirds">{field('workers', 'Worker count', 'number')}{field('cpus', 'CPUs per worker', 'number')}{field('memory', 'RAM budget per worker')}</div><p className="muted small">RAM is a soft budget. Workers may exceed it and show a warning. Increase the budget or reduce worker count if the machine runs short of RAM.</p>
      <details><summary>Build and guest assets</summary><div className="form-grid">{field('build_jobs', 'Build parallelism', 'number')}{field('disk', 'Existing guest disk (optional)', 'text', false)}{field('kernel', 'Existing kernel (optional)', 'text', false)}</div><p className="muted small">The image preparation and calibration machine builds the shared SDK and application images. Other machines receive those images and prepare their guest disks locally. Leave paths empty to build a guest and download the pinned kernel.</p></details>
      <div className="dialog-actions"><button type="button" className="button" onClick={close}>Cancel</button><button disabled={saving} className="button primary">{saving ? 'Saving…' : 'Save machine'}</button></div>
    </form>
  </Dialog>;
}

function ExperimentDialog({ initial, machines, close, save }: { initial?: Record<string, unknown>; machines: Machine[]; close: () => void; save: (body: unknown) => Promise<void> }) {
  const [config, setConfig] = useState(initial ? JSON.stringify(initial, null, 2) : '');
  const [name, setName] = useState('Memory contention');
  const [selected, setSelected] = useState(machines.map(m => m.id));
  const [calibration, setCalibration] = useState(machines[0]?.id || '');
  const calibrationMachine = selected.includes(calibration) ? calibration : selected[0] || '';
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
      <aside className="experiment-options"><h3>Run on these machines</h3><p className="muted small">Cases are assigned dynamically. Choose a machine to prepare the SDK and application images and calibrate memory. Both images are copied to the other machines; each machine prepares its guest disk locally.</p>
        {!machines.length && <p>Add a machine before queueing.</p>}{machines.map(m => <label className="machine-choice" key={m.id}><input type="checkbox" checked={selected.includes(m.id)} onChange={e => setSelected(e.target.checked ? [...selected, m.id] : selected.filter(id => id !== m.id))} /><span>{m.name}<small>{m.workers} workers · {m.cpus} CPU · {m.memory}</small></span></label>)}
        <div className="calibration-field"><label htmlFor="calibration-machine">Image preparation & calibration machine</label><select id="calibration-machine" value={calibrationMachine} disabled={!selected.length} onChange={e => setCalibration(e.target.value)}>
          {!selected.length && <option value="">Select a machine above</option>}
          {machines.filter(m => selected.includes(m.id)).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select></div>
        <div className="preview"><h3>Expanded cases</h3>{points ? <><p className="preview-count">{points.length}<span>cases</span></p><div className="preview-list">{points.map((p, i) => <div key={i}><span>{String(p.id)}</span><span>{String(p.pattern || 'baseline')} / {Math.round(Number(p.pressure || 0) * 100)}%</span></div>)}</div></> : <p className="muted small">Validate the configuration to preview the case queue.</p>}</div>
      </aside></div>
    {error && <p className="editor-error" role="alert">{error}</p>}
    <div className="dialog-actions"><span className="muted small">One experiment at a time. Finalized runs retain results and release working files.</span><button className="button" disabled={busy} onClick={validate}><Check size={16} />Validate</button><button className="button primary" disabled={busy || !name.trim() || !selected.length} onClick={async () => { const validated = await validate(); if (validated) { setBusy(true); await save({ name, config: validated, machines: selected, calibration_machine: calibrationMachine }); setBusy(false); } }}><Play size={15} />Start experiment</button></div>
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
