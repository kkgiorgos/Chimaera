"""Discord presentation captured with the event, so retries retain original context."""
from collections import Counter
import re
import time

from common import event


def plain(value, limit=300):
    """Keep names and remote errors from changing Markdown layout or mentioning users."""
    value = re.sub(r'\s+', ' ', str(value)).strip()
    value = re.sub(r'([\\`*_{}\[\]()<>#|~])', r'\\\1', value)
    return value if len(value) <= limit else value[:limit - 1].rstrip('\\') + '…'


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours}h {minutes:02d}m' if hours else f'{minutes}m {seconds:02d}s'


def content(item):
    """Also format persisted events created before rich notifications were added."""
    return item.get('notification') or (
        f"**Chimaera · {plain(item.get('level', 'info').title())}**\n"
        f"{plain(item['message'], 1600)}\n"
        f"<t:{int(item['time'])}:f>")


def notification_event(store, message, level='info', *, title, experiment=None,
                       case=None, attempt=None, machine=None, detail=None):
    now = time.time()
    icon = '🔴' if level == 'error' else '⚠️' if level == 'warning' else '🔹'
    lines = [f'{icon} **Chimaera · {plain(title, 100)}**', f'<t:{int(now)}:f>']

    def field(label, value):
        lines.append(f'**{label}:** {plain(value)}')

    if experiment:
        field('Experiment', f"{experiment['name']} ({experiment['id']})")
    if case:
        field('Case', case['id'])
    if machine:
        field('Machine', f"{machine['name']} ({machine['id']})")
    if detail:
        field('Details', detail)
    if attempt:
        field('Attempt', attempt['id'])
        if attempt.get('status'):
            field('Attempt status', attempt['status'])
        if attempt.get('started') is not None:
            field('Elapsed', duration((attempt.get('finished') or now) - attempt['started']))
        resources = [f"{attempt['cpus']} CPUs" if attempt.get('cpus') else '',
                     f"{attempt['memory']} RAM" if attempt.get('memory') else '']
        if any(resources):
            field('Worker allocation', ' · '.join(x for x in resources if x))
        if attempt.get('memory_peak_bytes') is not None:
            field('Peak RAM', f"{attempt['memory_peak_bytes'] / 1024**3:.2f} GiB")
        if attempt.get('memory_budget_bytes') is not None:
            field('RAM budget', f"{attempt['memory_budget_bytes'] / 1024**3:.2f} GiB")
        if attempt.get('exit_code') is not None:
            field('Exit code', attempt['exit_code'])
        if attempt.get('oom'):
            field('Failure reason', 'Memory limit exceeded (OOM)')
        progress = attempt.get('progress', {})
        if progress.get('phase'):
            field('Phase', progress['phase'])
        if progress.get('average_speed') is not None:
            field('Simulation speed', f"{progress['average_speed']:.4g}× real time")
        if attempt.get('collected'):
            field('Results', 'Collected on controller')
    if experiment:
        counts = Counter(c['status'] for c in experiment['cases'])
        total = len(experiment['cases'])
        field('Progress', f"{counts['completed']}/{total} completed" +
              (f" ({counts['completed'] / total:.0%})" if total else ''))
        field('Case states', ' · '.join(f'{n} {s.replace("_", " ")}' for s, n in sorted(counts.items())) or 'No cases')
        field('Experiment status', experiment['status'].replace('_', ' '))
        if experiment.get('activated') is not None:
            field('Experiment elapsed', duration((experiment.get('finished') or now) - experiment['activated']))
        if 'eta_seconds' in experiment:
            eta = experiment['eta_seconds']
            field('Estimated remaining', duration(eta) if eta is not None else 'Awaiting sufficient data')
            if experiment.get('eta_basis'):
                field('ETA basis', experiment['eta_basis'])
    if case and case.get('point'):
        point = case['point']
        field('Parameters', point.get('label') or ' · '.join(
            f'{k}: {point[k]}' for k in ('cpu_type', 'pattern', 'pressure') if k in point))
    # Keep whole fields and reserve room for an explicit omission marker.
    body = ''
    for line in lines:
        if len(body) + len(line) + 1 > 1800:
            body += '\n… Additional details available in the orchestrator.'
            break
        body += ('\n' if body else '') + line
    return event(store, message, level, notify=True, notification=body)
