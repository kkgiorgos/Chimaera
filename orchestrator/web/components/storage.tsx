'use client';
import { useEffect, useState } from 'react';
import type { State } from '@/lib/types';

type Inventory = {
  filesystem: { total: number; used: number; free: number };
  assets: { id: string; kind: string; name: string; bytes: number | null; protected: boolean; reason: string }[];
  experiments: { id: string; bytes: number }[];
};
const busy = ['preparing', 'running', 'stopping', 'needs_attention', 'queued'];
const size = (n: number) => n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GiB` : `${(n / 1024 ** 2).toFixed(1)} MiB`;

export default function Storage({ state, command, confirm }: {
  state: State | null;
  command: (kind: string, body: unknown) => Promise<boolean>;
  confirm: (value: { title: string; description: string; action: () => void }) => void;
}) {
  const [location, setLocation] = useState('');
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [revision, setRevision] = useState(0);
  const machine = state?.machines.find(m => m.id === location);
  const cleaning = Boolean(machine?.cleaning);
  useEffect(() => {
    const abort = new AbortController();
    setInventory(null); setSelected([]); setError(''); setLoading(true);
    fetch(`/api/storage${location ? `/${location}` : ''}`, { signal: abort.signal, cache: 'no-store' })
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Storage scan failed');
        if (!abort.signal.aborted) setInventory(result);
      }).catch(e => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [location, revision, cleaning]);
  const locked = cleaning || Boolean(state?.experiments.some(e => busy.includes(e.status) && e.machines.includes(location)));
  const removable = inventory?.assets.filter(a => !a.protected) || [];
  const diskBytes = inventory?.assets.filter(a => selected.includes(a.id) && a.kind === 'disk').reduce((n, a) => n + (a.bytes || 0), 0) || 0;
  return <section className="storage-panel">
    <div className="page-heading"><div><h1>Storage</h1><p>Find large assets and release space on each machine or the dashboard server.</p></div>
      <button className="button" disabled={loading || cleaning} onClick={() => setRevision(n => n + 1)}>Refresh usage</button></div>
    <label className="storage-location">Storage location<select value={location} onChange={e => setLocation(e.target.value)}>
      <option value="">Dashboard server</option>{state?.machines.map(m => <option key={m.id} value={m.id} disabled={!m.deployed}>{m.name}</option>)}
    </select></label>
    {loading && <p role="status">Measuring allocated disk space…</p>}
    {error && <p className="machine-error" role="alert">{error}</p>}
    {machine?.cleanup_error && <p className="machine-error" role="alert">Cleanup failed: {machine.cleanup_error}</p>}
    {cleaning && <p role="status">Removing assets… Usage will refresh when cleanup finishes.</p>}
    {inventory && <>
      <div className="storage-summary"><strong>{size(inventory.filesystem.free)} free</strong><span>{size(inventory.filesystem.used)} used of {size(inventory.filesystem.total)}</span>
        <progress aria-label="Filesystem space used" value={inventory.filesystem.used} max={inventory.filesystem.total} /></div>
      <p className="muted small">Filesystem usage includes other files on this volume. File sizes below count allocated disk blocks. Docker image sizes include shared layers and cannot be added together to predict reclaimed space.</p>
      {location && <>
        <div className="section-title"><h2>Cached images & guest disks</h2><button className="button danger-button" disabled={locked || !selected.length} onClick={() => confirm({
          title: `Remove ${selected.length} assets?`, description: `Permanently remove the selected assets from ${machine?.name}. Selected guest disks occupy ${size(diskBytes)}. Docker will reclaim layers when no other image uses them. Dependencies are checked again before removal.`,
          action: () => { void command('clean_assets', { id: location, asset_ids: selected }); },
        })}>Remove selected ({selected.length})</button></div>
        {locked && <p className="muted">Stop experiments on this machine before removing cached assets.</p>}
        <label className="storage-select"><input type="checkbox" disabled={locked || !removable.length} checked={removable.length > 0 && selected.length === removable.length} onChange={e => setSelected(e.target.checked ? removable.map(a => a.id) : [])} />Select all unused assets</label>
        <div className="storage-items">{inventory.assets.map(a => <label className="storage-row" key={a.id}>
          <input type="checkbox" disabled={locked || a.protected} checked={selected.includes(a.id)} onChange={e => setSelected(prev => e.target.checked ? [...prev, a.id] : prev.filter(id => id !== a.id))} />
          <span><strong>{a.name}</strong><small>{a.kind === 'disk' ? 'Guest disk' : 'Docker image'} · {a.protected ? a.reason : 'Unused cache · can be rebuilt'}</small></span><strong>{a.bytes == null ? 'Size unavailable' : size(a.bytes)}</strong>
        </label>)}</div>
        {!inventory.assets.length && <p className="muted">No managed cached assets found.</p>}
      </>}
      <h2>{location ? 'Remote experiment copies' : 'Collected results & experiment files'}</h2>
      <p className="muted small">{location ? 'Removing remote copies releases their inputs, checkpoints and output. Then refresh usage to remove cached assets that are no longer referenced.' : 'Deleting removes collected results, staging files and shared experiment assets from the dashboard server. Experiment history remains.'}</p>
      <div className="storage-items">{inventory.experiments.slice().sort((a, b) => b.bytes - a.bytes).filter(e => e.bytes > 0).map(item => {
        const experiment = state?.experiments.find(e => e.id === item.id);
        const disabled = !experiment || busy.includes(experiment.status) || cleaning;
        return <div className="storage-row" key={item.id}><span><strong>{experiment?.name || item.id}</strong><small>{experiment?.status.replaceAll('_', ' ') || 'Unknown experiment'} · {item.id}</small></span><strong>{size(item.bytes)}</strong>
          <button className="text-button danger-text" disabled={disabled} onClick={() => confirm({ title: location ? 'Remove remote experiment copies?' : 'Delete collected experiment files?',
            description: location ? 'Permanently remove this experiment’s remote files and stopped containers on this machine. Uncollected output will be discarded. Collected dashboard results stay available.' : 'Permanently delete this experiment’s collected results and shared files from the dashboard server. Download any results you want to keep first.',
            action: () => { void command(location ? 'cleanup' : 'delete', { id: item.id, ...(location ? { discard_uncollected: true, machine: location } : {}) }); },
          })}>{location ? 'Remove remote copies' : 'Delete files'}</button></div>;
      })}</div>
      {!inventory.experiments.some(e => e.bytes > 0) && <p className="muted">No experiment files found.</p>}
      <p className="muted small">Refresh usage after removing experiment files. Existing supervisors must be redeployed to enable storage inspection.</p>
    </>}
  </section>;
}
