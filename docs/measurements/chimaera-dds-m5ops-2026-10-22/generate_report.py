#!/usr/bin/env python3
"""Publish a finite-window comparison without hiding loss, source lag or failures."""
import csv
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / '.chimaera-m5'
OUT = ROOT / 'docs/measurements/chimaera-dds-m5ops-2026-10-22'
SOURCE = WORK / 'timing-short-r3-report'
LABELS = {
    'original_m5': 'Original m5', 'streamed_m5': 'Streamed m5',
    'initialized_m5_ready': 'Initialized m5 (READY)', 'integrated_dma': 'Integrated DMA',
    'original_dds_dma': 'Original DDS/DMA', 'copy_reduced_dds_dma': 'Copy-reduced DDS/DMA',
    'scheduled_dds_dma': 'Scheduling DDS/DMA', 'direct_dds_m5ops': 'New DDS/m5ops',
}
STREAMS = ('left_image', 'right_image', 'joint_states', 'effort_command', 'clock')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(n, digits=2):
    return '—' if n is None else f'{n:.{digits}f}'


def window(row):
    if not row.get('metrics'):
        return dict(implementation=row['case'].get('implementation'),
                    label=LABELS.get(row['case'].get('implementation')), status=row['status'],
                    case=row['case']['name'], errors=row['errors'])
    metrics = row['metrics']
    stereo = sum(metrics[s]['observation']['body_MB_per_observation_second'] for s in ('left_image', 'right_image'))
    stereo_cohort = sum(metrics[s]['delivered_body_MB_per_source_second'] for s in ('left_image', 'right_image'))
    return dict(implementation=row['case']['implementation'], label=LABELS[row['case']['implementation']],
        status=row['status'], case=row['case']['name'], path=row['path'], errors=row['errors'],
        comparison_eligible=row.get('comparison_eligible'), comparison_exclusion=row.get('comparison_exclusion'),
        native_references=row.get('native_references'), stereo_body_MB_per_observation_second=stereo,
        stereo_body_MB_per_source_second=stereo_cohort,
        execution_wall_seconds=row.get('execution_wall_seconds'), wall_seconds=row.get('wall_seconds'),
        metrics=metrics, cpu_evidence=row.get('cpu_evidence'),
        config=row['config'], raw_sha256=row['raw_sha256'])


def control_summary(metric):
    return number(metric['latency']['p50_ms']) + ' / ' + number(metric['latency']['max_ms'])


report = json.loads((SOURCE / 'comparison.json').read_text())
rows = report['attempts']
base, bulk, native = [], [], []
for variant in LABELS:
    peers = [r for r in rows if r['case']['implementation'] == variant
             and r['case']['config']['mode'] == 'chimaera']
    base += [window(r) for r in peers if r['case']['cell'] == 'short-ball']
    bulk += [window(r) for r in peers if r['case']['cell'] == 'short-bulk8m']
native = [window(r) for r in rows if r['case']['config']['mode'] == 'native_free']
assert len(base) == 8 and len(bulk) == 2 and len(native) == 2
contrasts = json.loads((WORK / 'short-r3-loaded-controls.json').read_text())
failures = [dict(case=r['case']['name'], status=r['status'], errors=r['errors'])
            for r in rows if r['status'] != 'complete' or r['errors']]
result = dict(schema_version=1, scope='User-approved short TimingSimpleCPU transient screen',
    status='completed' if not failures else 'completed_with_recorded_failure', failures=failures,
    baseline=base, bulk=bulk, native=native, loaded_control_comparisons=contrasts,
    comparisons=report['comparisons'], tail_decisions=report['tail_decisions'],
    input_sha256=report['input_sha256'], physical_host=report['physical_host'],
    definitions=report['definitions'],
    provenance={name:sha(WORK/name) for name in ('selection.r3.lock.json','short-r3.json',
        'timing-approved-r3.json','tooling-source-r3.tar.gz','short-r3-freeze-audit.json')},
    limitations=['One 0.2-second source window; 0.05-second warmup, 0.1-second drain.',
        'Transient observations, not maximum capacity, sustainable throughput or confidence-based tail nonregression.',
        'Latency summaries include delivered samples only. Read missing receipts and missed releases alongside them.',
        'Receipt-time rates exclude drain and may include delayed warmup samples.',
        'No stepped native cases; two explicit shared free-running SHM references.',
        'Bulk is a single scheduled 8MiB sample per direction in the source window, plus warmup traffic.',
        'The new m5ops payload path uses functional/coherent copying instead of timed DMA payload requests.',
        'READY binary identity is pinned; exact retained-source correspondence remains unverified.'])
OUT.mkdir(parents=True, exist_ok=True)
(OUT/'timing-short-results.json').write_text(json.dumps(result,indent=2)+'\n')
shutil.copyfile(SOURCE/'comparison.csv',OUT/'timing-short-metrics.csv')
metrics_csv=OUT/'timing-short-metrics.csv'
metrics_csv.write_bytes(metrics_csv.read_bytes().replace(b'\r\n',b'\n'))
with (OUT/'timing-short-comparisons.csv').open('w',newline='') as handle:
    comparisons=report['comparisons']
    fields=list(dict.fromkeys(k for row in comparisons for k in row))
    writer=csv.DictWriter(handle,fieldnames=fields,lineterminator='\n')
    writer.writeheader()
    writer.writerows(comparisons)

lines=['# Short TimingSimpleCPU comparison','',
    '**Outcome:** all eight ball-like baselines completed; eleven of twelve total '
    'cases completed. The new backend improved baseline latency versus scheduling '
    'DDS/DMA, but the no-added-control-latency objective remains unmet under 8 MiB '
    'traffic. Historical scheduling DMA failed that bulk case with a controller '
    'queue limit, so there is no numeric bulk speedup comparison.', '',
    'This is the user-approved replacement for the cancelled long suite. It contains '
    'eight ball-like implementation runs, two focused bidirectional 8MiB payload runs, and '
    'two shared native-free SHM references. No stepped native or UDP reference ran. '
    'KVM iteration results are [reported separately](kvm-results.md).','',
    'Each case uses a 0.2s source window, 0.05s warmup and 0.1s drain. The workload is '
    '480×360 RGB stereo at 30 Hz, joints/efforts at 250 Hz and clock at 1 kHz. Hardware is two '
    'TimingSimpleCPUs at the established 3GHz setting, 16KiB 8-way L1I/L1D, 256KiB '
    '16-way L2, MESI_TWO_LEVEL and 3 GiB DDR3_1600. Coupling is 2 ms and physics is 1 ms. '
    'All cases use the same 64 MiB SHM/large-message DDS profile and reliable/lossless policy. '
    'Full payload checking is disabled for these performance runs; KVM qualification '
    'with byte verification preceded them.','',
    'Throughput below is stereo body bytes received in the fixed callback-time interval '
    '[0.05s,0.25s), decimal MB/s, excluding drain. Latency is application publication '
    'to callback in simulation-clock milliseconds. These are short transient observations, '
    'not sustainable capacity or a confidence-based tail claim.','',
    '| Implementation | Stereo receipt-window MB/s | Joints median / max ms | Effort median / max ms | Clock median / max ms |',
    '|---|---:|---:|---:|---:|']
for row in base:
    if not row.get('metrics'):
        lines.append(f"| {row['label']} | {row['status']} | — | — | — |")
        continue
    m=row['metrics']
    lines.append('| '+row['label']+' | '+number(row['stereo_body_MB_per_observation_second'])+' | '+
        ' | '.join(control_summary(m[s]) for s in ('joint_states','effort_command','clock'))+' |')
lines += ['',
    'Every baseline delivered its twelve measured stereo source-cohort frames '
    '(31.104 MB/s when normalized by the source duration, including any drain '
    'receipts). Higher receipt-window values for older implementations reflect '
    'delayed warmup frames entering the window and do not establish a throughput '
    'advantage. This offered load does not measure maximum capacity.', '',
    'Against scheduling DDS/DMA, median latency changed from 0.42 to 0.18 ms for '
    'joints, 0.47 to 0.26 ms for clocks, and 1.40 to 1.29 ms for efforts. The new '
    'backend recorded the lowest observed maximum for each of the five streams '
    'in these baseline windows. This is descriptive: copy-reduced DDS/DMA still '
    'had a lower effort median (1.12 ms), and one window does not establish '
    'repeatable tail non-regression.', '',
    'Source and delivery counts are essential with these small windows. Each cell below '
    'is **received / published**, followed by **missed scheduled releases**. Published '
    'cohorts are selected by actual publication time; delayed warmup sends can enter them. '
    'Absent releases are not counted as network loss.','',
    '| Implementation | Left image | Right image | Joints | Effort | Clock |',
    '|---|---:|---:|---:|---:|---:|']
for row in base:
    if not row.get('metrics'):
        continue
    cells=[]
    for stream in STREAMS:
        m=row['metrics'][stream]
        cells.append(f"{m['received']}/{m['published']}; {m['missed_source_releases']}")
    lines.append('| '+row['label']+' | '+' | '.join(cells)+' |')
lines += ['',
    '## Focused bulk transfer','',
    'Each bulk case adds one scheduled8MiB sample per direction inside the measured '
    'source window, plus a sample during warmup. It is a transient overlap test, '
    'rather than the cancelled four-image burst test. Body rates exclude framing. '
    'Source-cohort receipts may arrive during drain; receipt-time rates do not include them.','',
    '| Implementation | Direction | Cohort received / published | Receipt-window samples | Receipt-window MB/s | Latency median / max ms |',
    '|---|---|---:|---:|---:|---:|']
for row in bulk:
    if not row.get('metrics'):
        lines.append('| '+row['label']+' | both | failed | — | — | controller queue limit |')
        continue
    for direction in ('h2g','g2h'):
        m=row['metrics']['bulk_image_'+direction]
        lines.append('| '+row['label']+' | '+direction+' | '+f"{m['received']}/{m['published']}"+' | '+str(m['observation']['receipts'])+' | '+
                     number(m['observation']['body_MB_per_observation_second'])+' | '+control_summary(m)+' |')
lines += ['',
    'For the new backend, the measured H→G body arrived at 319.00 ms and the G→H '
    'body at 282.00 ms, both during drain. The receipt-window body rates above '
    'therefore describe the earlier warmup payloads, not those measured-cohort '
    'bodies. Both measured bodies were delivered.', '',
    'A receipt window can include a delayed warmup payload as well as the measured '
    'source-cohort payload. A larger receipt-window rate in this transient test can '
    'therefore reflect warmup spillover rather than a faster implementation. Read '
    'the receipt count, cohort counts and latency together; do not rank sustained '
    'bulk bandwidth from this window.', '',
    'The following control contrasts use each implementation’s ball-only case as its '
    'unloaded baseline, with exactly matched duration, DDS profile, camera rates and CPU '
    'geometry. Different publication counts and missing samples can censor latency; '
    'they must not be interpreted as an improvement.','',
    '| Implementation | Stream | Ball-only median / max ms | With bulk median / max ms | Ball-only vs bulk received / published |',
    '|---|---|---:|---:|---:|']
for row in contrasts:
    a,b=row['baseline_metric'],row['loaded_metric']
    counts=f"{a['received']}/{a['published']} → {b['received']}/{b['published']}"
    lines.append('| '+LABELS[row['implementation']]+' | '+row['stream']+' | '+
                 control_summary(a)+' | '+control_summary(b)+' | '+counts+' |')
lines += ['', '## Remaining blocking stages', '',
    'The transport has independent control/bulk readers, send locks and credits, '
    'but the benchmark application uses one SingleThreadedExecutor for merged '
    'source publication and typed subscription takes. A large publish can block '
    'other source releases; typed deserialization occurs before the callback '
    'timestamp. Raw events show bulk-adjacent guest source gaps of roughly '
    '72–83 ms and receive callback gaps around 33–34 ms before bulk callbacks. '
    'Fast DDS also has a participant-wide send critical section above the '
    'transport lanes. These are source-established coupling opportunities, '
    'not a measured attribution of every millisecond.', '',
    'The next bounded diagnostic should separate control and image/bulk '
    'application execution and DDS participants on both sides, while preserving '
    'source schedules and recording per-stage entry/return times. Ordinary camera '
    'images also need consideration. Merely changing to a multithreaded executor '
    'with the same default mutually exclusive callback group is insufficient. '
    'No such application change or additional benchmark is included in these results.', '',
    'Static evidence: [bulk-stall review](bulk-stalls-review.md) and '
    '[historical queue review](dma-queue-review.md). The former independently '
    'recomputed the direct cases’ raw hashes and quoted latency summaries. '
    'There is no per-stage profiling trace proving the time split. Performance '
    'cases disable full payload verification, including the 8 MiB cases; the '
    'earlier KVM byte-verification workload used the nominal ball-like streams.', '',
    '## Evidence and interpretation','',
    '**Source lag also matters.** Under bulk, twelve scheduled effort releases '
    'were absent from the source window, although fifty actually published '
    'effort-cohort samples arrived. Delayed warmup sends can enter that cohort. '
    'Effort publication-to-callback latency stayed near 1 ms while maximum '
    'schedule-to-callback age rose from 4 to 80 ms and maximum release lag reached '
    '78.47 ms. Joint and clock age maxima reached 123.38 and 127.41 ms. Low '
    'publication-to-callback latency alone therefore does not establish control '
    'timeliness.', '',
    '**The historical bulk failure is a resource-limit result.** The retained '
    'DMA controller has a shared 32 MiB outgoing cap and a separate 32 MiB '
    'incoming cap. Its outgoing queue is the strongest source-based explanation '
    'for the failure, but no C++ stack or queue occupancy was recorded. There '
    'is no runtime knob for those controller byte caps. The new host controller '
    'instead permits 128 MiB outgoing and 640 MiB incoming per independent lane. '
    'The historical DDS transport also advertises a smaller packet limit. '
    'Queue capacity, packetization and admission differ; successful new bulk '
    'delivery cannot be attributed solely to m5ops or converted into a numeric '
    'DMA-to-m5ops speedup.', '',
    'Each completed guest attempt is checked against the retained CPU-origin list, '
    'cache/DRAM configuration, binary identities and raw-file hashes. Dormant KVM boot '
    'CPUs do not establish the active timing model. Shared native-free references are '
    'accepted only when their declared case, settings, DDS XML and common endpoint/clock/RMW '
    'hashes match. Native timestamps have 1 ms simulation-clock quantization; zero is not '
    'zero wall latency.','',
    'The m5ops backend transfers payload through functional/coherent access without timed '
    'DMA payload memory requests. Host copies and guest DDS/instruction work remain; '
    'this is a changed payload memory-cost model, not a claim of physically free transfer. '
    'The unprefaulted FINAL initialized-buffer pair remains a separate failed KVM '
    'diagnostic; the comparison uses the historically benchmarked READY binary. Exact '
    'READY binary/source correspondence is unverified.','',
    'One short window per cell cannot establish maximum or sustainable throughput, '
    'repeatability, or tail non-regression. Camera percentiles have only a few samples; '
    'the table therefore emphasizes medians and observed maxima. The JSON/CSV also '
    'retain age, release lag, deadlines, losses, duplicates, reordering and backlog. '
    'Guest statistics retain their original dump scope, which may include warmup, '
    'drain and export; overlapping dumps are not summed.','',
    f"Selection lock SHA256: `{result['provenance']['selection.r3.lock.json']}`.",
    f"Matrix SHA256: `{result['provenance']['short-r3.json']}`.", '',
    'Artifacts: [summary JSON](timing-short-results.json), '
    '[per-stream metrics CSV](timing-short-metrics.csv), '
    '[matched comparisons CSV](timing-short-comparisons.csv). '
    'Raw suite: `.chimaera-m5/timing-short-r3/`; validated full report: '
    '`.chimaera-m5/timing-short-r3-report/comparison.json`; '
    'tooling source: `.chimaera-m5/tooling-source-r3.tar.gz`.','']
if failures:
    lines += ['Incomplete or invalid attempts remain visible:', '',
              *[f"- {r['case']}: {r['status']} — {'; '.join(r['errors'])}" for r in failures], '']
(OUT/'timing-short-results.md').write_text('\n'.join(lines))
print(json.dumps(dict(status=result['status'],attempts=len(rows),failures=failures,
                      output=str(OUT/'timing-short-results.md'))))
