/* Buildless, offline comparison; retain the established motion replay. */
(() => {
  const data = window.EXPERIMENT_DATA;
  const byId = new Map(data.trials.map(trial => [trial.id, trial]));
  if (!data.points.some(point => point.rates)) return;
  const el = id => document.getElementById(id);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const format = (value, digits = 1) => finite(value) ? value.toFixed(digits) : '—';
  el('memory-panel').hidden = false;
  el('report-title').textContent = 'Ball catching under memory interference';
  el('report-description').textContent = 'Compare binary task outcomes, guest DRAM latency, processing times, message latency, and delivered control updates.';
  el('points-title').textContent = 'Benchmark cases';
  for (const [id, key] of [['memory-throw', 'throw'], ['memory-rates', 'rates']]) {
    for (const name of [...new Set(data.points.map(point => point[key].name))]) {
      const option = document.createElement('option');
      option.value = name; option.textContent = name; el(id).append(option);
    }
  }
  const timing = stats => stats ? [stats.mean_ms, stats.p95_ms, stats.p99_ms].map(value => format(value, 3)).join(' / ') : 'Not recorded';
  const trialFor = point => byId.get(point.trials[0]);
  const comparable = (a, b) => a.throw.name === b.throw.name && a.rates.name === b.rates.name &&
    (a.cpu_type || 'timing') === (b.cpu_type || 'timing') &&
    (a.ramulator_sha256 || null) === (b.ramulator_sha256 || null);
  function svgElement(name, attributes, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function render() {
    const metric = el('memory-metric').value;
    const points = data.points.filter(point =>
      (el('memory-throw').value === 'all' || point.throw.name === el('memory-throw').value) &&
      (el('memory-rates').value === 'all' || point.rates.name === el('memory-rates').value));
    el('memory-rows').replaceChildren();
    const observations = [];
    for (const point of points) {
      const trial = trialFor(point);
      const benchmark = trial?.benchmark;
      const memory = benchmark?.memory;
      const baseline = data.points.find(candidate => comparable(candidate, point) && candidate.pressure === 0);
      const baseMemory = baseline && trialFor(baseline)?.benchmark?.memory;
      const ratio = finite(memory?.total_p99_ns) && baseMemory?.total_p99_ns > 0 ?
        memory.total_p99_ns / baseMemory.total_p99_ns : null;
      const row = document.createElement('tr');
      const outcome = trial ? (trial.result.success ? 'CATCH' : 'MISS') : point.status.replaceAll('_', ' ');
      const values = [point.label, outcome,
        `${point.rates.camera_hz} / ${point.rates.planner_hz} / ${point.rates.control_hz}`,
        `${Math.round(point.pressure * 100)}%`, format(memory?.aggressor_accepted_gbps, 2),
        finite(memory?.dram_bus_peak_fraction) ? `${(100 * memory.dram_bus_peak_fraction).toFixed(1)}%` : '—',
        format(memory?.total_mean_ns), format(memory?.total_p95_ns), format(memory?.total_p99_ns),
        finite(ratio) ? `${ratio.toFixed(2)}×` : '—', format(benchmark?.commands?.delivered_control_hz),
        timing(trial?.summary?.processing?.['Perception + estimation']),
        timing(trial?.summary?.processing?.Planning), timing(trial?.summary?.processing?.['Arm control']),
        format(trial?.summary?.observation_age?.p95_ms),
        finite(benchmark?.wall_seconds) ?
          `${benchmark.wall_seconds_lower_bound ? '≥ ' : ''}${format(benchmark.wall_seconds / 60)} min` : '—',
        trial?.summary?.timing_coverage?.status === 'partial' ?
          `Partial · ${trial.summary.timing_coverage.missing_control_timings} missing control timings` :
          trial?.summary?.timing_coverage?.status === 'no_missing_delivered_control_timings' ? 'Control timings match deliveries' : 'Not checked',
        timing(trial?.summary?.transport_latency?.streams?.find(stream => stream.topic === '/robot/effort_command')?.transport?.latency),
        (() => { const stream = trial?.summary?.transport_latency?.streams?.find(stream => stream.topic === '/robot/effort_command')?.transport; return stream&&stream.status!=='not_recorded' ? `${stream.received} / ${stream.published}` : 'Not recorded'; })()];
      for (const [index, value] of values.entries()) {
        const cell = document.createElement('td'); cell.textContent = value;
        if (index === 1 && trial) cell.className = trial.result.success ? 'good' : 'bad';
        row.append(cell);
      }
      row.title = `Processing clock: ${trial?.summary?.processing_clock || 'not recorded'}; memory: ${benchmark?.memory_profile?.standard || 'see execution configuration'}, peak ${format(benchmark?.peak_gbps)} GB/s` +
        (benchmark?.wall_seconds_lower_bound ? '; wall time through last recorded exchange; cleanup/export duration unavailable' : '');
      row.tabIndex = 0;
      row.setAttribute('aria-label', `Inspect ${point.label}`);
      const select = () => el('points').children[data.points.indexOf(point)]?.click();
      row.onclick = select;
      row.onkeydown = event => { if (event.key === 'Enter') select(); };
      el('memory-rows').append(row);
      if (finite(memory?.[metric]) && finite(memory?.aggressor_accepted_gbps)) {
        observations.push({point, trial, x: memory.aggressor_accepted_gbps, y: memory[metric]});
      }
    }
    const svg = el('memory-chart'); svg.replaceChildren();
    const maxX = Math.max(1, ...observations.map(item => item.x)) * 1.1;
    const maxY = Math.max(1, ...observations.map(item => item.y)) * 1.1;
    const x = value => 75 + 690 * value / maxX;
    const y = value => 240 - 205 * value / maxY;
    svg.append(svgElement('path', {d: 'M75 25V240H765', fill: 'none', stroke: '#a6b5bd'}));
    for (let index = 0; index <= 4; index++) {
      const fraction = index / 4;
      svg.append(svgElement('text', {x: x(maxX * fraction), y: 259, fill: '#a6b5bd', 'text-anchor': 'middle', 'font-size': 12}, format(maxX * fraction)));
      svg.append(svgElement('text', {x: 65, y: y(maxY * fraction) + 4, fill: '#a6b5bd', 'text-anchor': 'end', 'font-size': 12}, format(maxY * fraction, 0)));
    }
    svg.append(svgElement('text', {x: 420, y: 282, fill: '#a6b5bd', 'text-anchor': 'middle', 'font-size': 12}, 'Achieved aggressor traffic · accepted GB/s'));
    svg.append(svgElement('text', {x: 80, y: 16, fill: '#a6b5bd', 'font-size': 12}, el('memory-metric').selectedOptions[0].textContent + ' · ns'));
    for (const observation of observations) {
      const dot = svgElement('circle', {cx: x(observation.x), cy: y(observation.y), r: 6,
        fill: observation.trial.result.success ? '#bbef73' : '#efa493'});
      dot.append(svgElement('title', {}, `${observation.point.label}: ${observation.trial.result.success ? 'CATCH' : 'MISS'}; ${format(observation.y)} ns`));
      svg.append(dot);
    }
    el('memory-note').textContent = observations.length ?
      'Green: catch. Coral: miss. Each point is one observation; no statistical reliability claim. Memory latency stops at backend completion and excludes upstream cache/fabric waiting. Delivered control cadence measures host receipts.' :
      'Memory measurements appear after a gem5 trial completes. Native preflight results check geometry and behavior on the host; they do not measure guest performance.';
  }
  for (const id of ['memory-throw', 'memory-rates', 'memory-metric']) el(id).onchange = render;
  el('memory-export').onclick = () => {
    const measurements = data.points.map(point => ({...point, benchmark: trialFor(point)?.benchmark,
      result: trialFor(point)?.result, timings: trialFor(point)?.summary}));
    const url = URL.createObjectURL(new Blob([JSON.stringify(measurements, null, 2)], {type: 'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = 'memory-comparison.json';
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  render();
})();
