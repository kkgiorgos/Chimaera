'use client';
import { useEffect, useState } from 'react';
import { Archive, Check, ChevronDown, Database, HardDrive, Image, LockKeyhole, RefreshCw, Search, Server, Trash2 } from 'lucide-react';
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
  confirm: (value: { title: string; description: string; actionLabel?: string; action: () => void }) => void;
}) {
  const [filter, setFilter] = useState<'all' | 'unused' | 'protected'>('all');
  const [query, setQuery] = useState('');
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
  const diskBytes = inventory?.assets.filter(a => selected.includes(a.id) && a.kind !== 'image').reduce((n, a) => n + (a.bytes || 0), 0) || 0;
  const assets = inventory?.assets || [];
  const visible = assets.filter(a => (filter === 'all' || (filter === 'protected' ? a.protected : !a.protected))
    && a.name.toLowerCase().includes(query.toLowerCase()));
  const selectable = visible.filter(a => !a.protected);
  const checkedCount = selectable.filter(a => selected.includes(a.id)).length;
  const selectedDiskCount = assets.filter(a => selected.includes(a.id) && a.kind !== 'image').length;
  const experimentFiles = inventory?.experiments.filter(e => e.bytes > 0).sort((a, b) => b.bytes - a.bytes) || [];
  const usedPercent = inventory ? Math.min(100, Math.round(inventory.filesystem.used / inventory.filesystem.total * 100)) : 0;
  const removeSelected = () => confirm({
    title: `Remove ${selected.length} assets?`, actionLabel: 'Remove selected',
    description: `Permanently remove the selected assets from ${machine?.name}. Selected guest files occupy ${size(diskBytes)}. Docker will reclaim layers when no other image uses them. Dependencies are checked again before removal.`,
    action: () => { void command('clean_assets', { id: location, asset_ids: selected }); },
  });
  return <section className="storage-panel">
    <div className="page-heading"><div><h1>Storage</h1><p>Make room for your next simulation.</p></div>
      <button className="button" disabled={loading || cleaning} onClick={() => setRevision(n => n + 1)}><RefreshCw size={15} />Refresh usage</button></div>
    <div className="storage-location-bar">
      <label className="storage-location"><Server size={18} /><span><small>Storage location</small><select value={location} onChange={e => { setLocation(e.target.value); setQuery(''); setFilter('all'); }}>
        <option value="">Dashboard server</option>{state?.machines.map(m => <option key={m.id} value={m.id} disabled={!m.deployed}>{m.name}</option>)}
      </select></span><ChevronDown size={14} /></label>
      <span className="storage-location-detail">{machine ? machine.target : 'Collected results and shared files'}</span>
    </div>
    {error && <div className="storage-notice storage-notice-error" role="alert"><div><strong>Couldn’t read storage usage</strong><p>{error}</p></div><button className="button compact" onClick={() => setRevision(n => n + 1)}>Try again</button></div>}
    {machine?.cleanup_error && <div className="storage-notice storage-notice-error" role="alert">Cleanup failed: {machine.cleanup_error}</div>}
    {loading && <div className="storage-loading" role="status"><HardDrive size={28} /><strong>Measuring disk usage</strong><span>Checking files and cached assets on this location.</span></div>}
    {cleaning && <div className="storage-notice" role="status"><RefreshCw size={16} /><span>Removing assets. Usage will refresh when cleanup finishes.</span></div>}
    {inventory && <>
      <div className={`storage-capacity ${usedPercent >= 90 ? 'storage-capacity-tight' : ''}`}>
        <div className="storage-capacity-heading"><div><HardDrive size={19} /><h2>Disk capacity</h2></div><span>{usedPercent}% used</span></div>
        <div className="storage-capacity-meter" role="meter" aria-label="Filesystem space used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={usedPercent}><span style={{ width: `${usedPercent}%` }} /></div>
        <div className="storage-capacity-labels"><span><i />{size(inventory.filesystem.used)} used <small>of {size(inventory.filesystem.total)}</small></span><strong>{size(inventory.filesystem.free)} free</strong></div>
        {usedPercent >= 90 && <p className="storage-capacity-hint">Space is running low. Start with unused guest disks or old experiment copies.</p>}
      </div>
      {location && <section className="storage-section" aria-labelledby="storage-cache-title">
        <div className="storage-section-heading"><div><h2 id="storage-cache-title">Cached assets <span>{assets.length}</span></h2><p>Guest disks, kernel/module pairs and images saved between runs.</p></div><span className="storage-protection-note"><LockKeyhole size={13} />Dependencies are protected</span></div>
        <div className="storage-tools"><div className="storage-filters" role="group" aria-label="Filter cached assets">
          {(['all', 'unused', 'protected'] as const).map(value => <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === 'all' ? 'All assets' : value === 'unused' ? 'Unused' : 'Protected'}<span>{value === 'all' ? assets.length : value === 'unused' ? removable.length : assets.length - removable.length}</span></button>)}
        </div><label className="storage-search"><Search size={15} /><input type="search" aria-label="Search cached assets" placeholder="Find an asset…" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
        {locked && <div className="storage-notice"><LockKeyhole size={15} /><span>Stop experiments on this machine to remove cached assets.</span></div>}
        <div className="storage-asset-table">
          <div className="storage-table-heading"><label><input type="checkbox" aria-label="Select visible unused assets" disabled={locked || !selectable.length} checked={selectable.length > 0 && checkedCount === selectable.length} ref={node => { if (node) node.indeterminate = checkedCount > 0 && checkedCount < selectable.length; }} onChange={e => setSelected(prev => e.target.checked ? [...new Set([...prev, ...selectable.map(a => a.id)])] : prev.filter(id => !selectable.some(a => a.id === id)))} /><span>Asset</span></label><span>Status</span><span>Size</span></div>
          {visible.map(a => <label className={`storage-asset-row ${selected.includes(a.id) ? 'storage-asset-selected' : ''}`} key={a.id}>
            <input type="checkbox" aria-label={`Select ${a.name}`} disabled={locked || a.protected} checked={selected.includes(a.id)} onChange={e => setSelected(prev => e.target.checked ? [...prev, a.id] : prev.filter(id => id !== a.id))} />
            <span className="storage-file-icon">{a.kind !== 'image' ? <HardDrive size={19} /> : <Image size={19} />}</span>
            <span className="storage-asset-name"><strong>{a.name}</strong><small>{a.kind === 'disk' ? 'Guest disk' : a.kind === 'kernel' ? 'Kernel and DMA module' : 'Docker image'}</small></span>
            <span className={`storage-asset-status ${a.protected ? 'is-protected' : ''}`} aria-label={a.protected ? `Protected: ${a.reason}` : 'Unused; can be rebuilt'} title={a.reason || 'Unused cache; can be rebuilt'}>{a.protected ? <LockKeyhole size={12} /> : <Check size={12} />}{a.protected ? 'Protected' : 'Unused'}</span>
            <strong className="storage-file-size">{a.bytes == null ? 'Unknown' : size(a.bytes)}</strong>
          </label>)}
          {!visible.length && <div className="storage-empty"><Database size={25} /><strong>{assets.length ? 'No matching assets' : 'No cached assets'}</strong><p>{assets.length ? 'Try a different name or filter.' : 'Images and guest disks will appear here after preparation.'}</p></div>}
        </div>
        <div className={`storage-selection ${selected.length ? 'has-selection' : ''}`}><span>{selected.length ? <><strong>{selected.length} selected</strong><small>{selectedDiskCount ? `${size(diskBytes)} in guest files` : 'Image layers may be shared'}</small></> : 'Select unused assets to remove them.'}</span><div>{selected.length > 0 && <button className="text-button" onClick={() => setSelected([])}>Clear</button>}<button className="button danger-button compact" disabled={locked || !selected.length} onClick={removeSelected}><Trash2 size={14} />Remove selected</button></div></div>
        <p className="storage-footnote">Image sizes include shared layers. Removing an image may free less space than shown.</p>
      </section>}
      <section className="storage-section" aria-labelledby="storage-experiments-title">
        <div className="storage-section-heading"><div><h2 id="storage-experiments-title">{location ? 'Remote experiment copies' : 'Experiment files'} <span>{experimentFiles.length}</span></h2><p>{location ? 'Remove old inputs and output to release their cached dependencies.' : 'Collected results, shared assets and staging files.'}</p></div><span className="storage-experiment-total">{size(experimentFiles.reduce((sum, e) => sum + e.bytes, 0))}</span></div>
        <div className="storage-experiment-list">{experimentFiles.map(item => {
          const experiment = state?.experiments.find(e => e.id === item.id);
          const disabled = !experiment || busy.includes(experiment.status) || cleaning;
          return <div className="storage-experiment-row" key={item.id}><Archive size={20} /><div className="storage-experiment-name"><strong>{experiment?.name || item.id}</strong><span><i className={`storage-state-dot ${disabled ? 'is-active' : ''}`} />{experiment?.status.replaceAll('_', ' ') || 'Unknown experiment'}<small title={item.id}>{item.id.slice(0, 8)}</small></span></div><strong className="storage-file-size">{size(item.bytes)}</strong>
            <button className="button compact storage-remove-copy" disabled={disabled} onClick={() => confirm({ title: location ? 'Remove remote experiment copies?' : 'Delete collected experiment files?', actionLabel: location ? 'Remove copies' : 'Delete files',
              description: location ? 'Permanently remove this experiment’s remote files and stopped containers on this machine. Uncollected output will be discarded. Collected dashboard results stay available.' : 'Permanently delete this experiment’s collected results and shared files from the dashboard server. Download any results you want to keep first.',
              action: () => { void command(location ? 'cleanup' : 'delete', { id: item.id, ...(location ? { discard_uncollected: true, machine: location } : {}) }); },
            })}><Trash2 size={13} />{location ? 'Remove copies' : 'Delete files'}</button></div>;
        })}</div>
        {!experimentFiles.length && <div className="storage-empty"><Archive size={25} /><strong>No experiment files here</strong><p>Files will appear as experiments are prepared and results collected.</p></div>}
        {experimentFiles.length > 0 && <p className="storage-footnote">{location ? 'Collected dashboard results stay available. Uncollected remote output is permanently discarded.' : 'Download results you want to keep before deleting. Experiment history stays available.'}</p>}
      </section>
      <details className="storage-details"><summary>About these measurements</summary><p>Sizes count allocated disk space, including sparse guest disks. Filesystem usage includes files outside Chimaera. Refresh usage after removing experiment files to see newly unused assets.</p></details>
    </>}
  </section>;
}
