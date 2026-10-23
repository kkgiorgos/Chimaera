"""Host-side removal helper, shipped over SSH so it also works without a supervisor."""
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess


OPTIONS = {'supervisor', 'experiment_data', 'images', 'guest_assets'}


def validate_cleanup(cleanup):
    if not isinstance(cleanup, dict) or set(cleanup) - OPTIONS or any(type(v) is not bool for v in cleanup.values()):
        raise ValueError('Invalid machine cleanup options')
    if any(cleanup.get(key) for key in OPTIONS - {'supervisor'}) and not cleanup.get('supervisor'):
        raise ValueError('Remove the supervisor before cleaning machine resources')
    return cleanup


def run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or 'Remote cleanup command failed')
    return result.stdout


def protected_mounts(containers, root, excluded):
    """The controller reads source from /repo; its durable results live in /data."""
    for container in containers:
        if container in excluded:
            continue
        labels = container.get('Config', {}).get('Labels') or {}
        for mount in container.get('Mounts', []):
            if not mount.get('Source'):
                continue
            controller_source = (
                labels.get('com.docker.compose.service') == 'controller'
                and labels.get('com.docker.compose.project.working_dir') == str(root / 'orchestrator')
                and mount['Source'] == str(root)
                and mount.get('Destination') == '/repo'
                and mount.get('RW') is False
            )
            # A read-only host filesystem view (e.g. a disk usage browser) is
            # observational; it must not pin every generated file on the host.
            host_view = mount['Source'] == '/' and mount.get('RW') is False
            if not controller_source and not host_view:
                yield Path(mount['Source']).resolve()


def remove(machine, cleanup):
    validate_cleanup(cleanup)
    root = Path(machine['repo'])
    data = root / '.chimaera-orchestrator'
    assets = data / 'assets'
    # Never traverse a replaced state directory into another part of the host.
    for path in (root, data, assets, data / 'experiments', data / 'agent.sqlite'):
        if path.is_symlink():
            raise ValueError('Cleanup path is a symlink: ' + str(path))
    ids = run(['docker', 'ps', '-aq']).split()
    containers = json.loads(run(['docker', 'inspect', *ids])) if ids else []
    supervisor = next((c for c in containers if c.get('Name', '').lstrip('/') == 'chimaera-supervisor'), None)
    if supervisor and not any(m.get('Source') == str(root) and m.get('Destination') == str(root)
                              for m in supervisor.get('Mounts', [])):
        raise ValueError('The supervisor belongs to a different repository')
    workers = [c for c in containers if (c.get('Config', {}).get('Labels') or {}).get('chimaera.experiment')]
    if any(c.get('State', {}).get('Running') for c in workers):
        raise ValueError('Stop all Chimaera workers on this host before removal')
    # Remove only stopped experiment containers mounted in this checkout.
    owned = [c for c in workers if any(Path(m.get('Source', '/')).is_relative_to(data) or m.get('Source') == str(root)
                                      for m in c.get('Mounts', []))]
    if cleanup.get('experiment_data'):
        experiments = data / 'experiments'
        protected = [Path(p).resolve() for p in (machine.get('disk'), machine.get('kernel')) if p]
        protected += list(protected_mounts(containers, root, [*owned, supervisor]))
        blocking = [str(p) for p in protected if p.is_relative_to(experiments.resolve()) or experiments.resolve().is_relative_to(p)]
        if blocking:
            raise ValueError('Experiment files contain a configured asset or are mounted by another container: '
                             + ', '.join(sorted(set(blocking))))
    if cleanup.get('supervisor') and supervisor:
        run(['docker', 'rm', '-f', supervisor['Id']], timeout=60)
    if cleanup.get('experiment_data'):
        if owned:
            run(['docker', 'rm', *[c['Id'] for c in owned]], timeout=60)
        if (data / 'experiments').exists():
            shutil.rmtree(data / 'experiments')
        # Keep release tombstones, but remove task records so a redeploy cannot
        # restart an accepted preparation whose working files were deleted.
        database = data / 'agent.sqlite'
        if database.exists():
            connection = sqlite3.connect(database)
            try:
                with connection:
                    tasks = connection.execute("SELECT body FROM records WHERE kind='tasks'").fetchall()
                    for (body,) in tasks:
                        experiment = json.loads(body)['experiment']
                        connection.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                                           ('released', experiment, 'true'))
                    connection.execute("DELETE FROM records WHERE kind IN ('tasks', 'lifecycle')")
            finally:
                connection.close()
    if cleanup.get('images'):
        listing = run(['docker', 'image', 'ls', '--format', '{{json .}}'])
        tags = set()
        for line in listing.splitlines():
            item = json.loads(line)
            # Podman's Docker-compatible CLI uses lowercase fields and qualifies
            # locally built repositories with localhost/.
            repository = item.get('Repository', item.get('repository', ''))
            tag = item.get('Tag', item.get('tag', ''))
            local_repository = repository.removeprefix('localhost/')
            if local_repository == 'chimaera' and tag.startswith(('sdk-', 'ball-', 'transfer-', 'orchestrator-')):
                tags.add(repository + ':' + tag)
            if local_repository == 'chimaera-supervisor' and tag == 'local':
                tags.add(repository + ':' + tag)
        if tags:
            # Docker refuses images in use. Never force deletion or prune the host.
            run(['docker', 'image', 'rm', *sorted(tags)], timeout=600)
        for marker in assets.glob('image-*.json'):
            image = json.loads(marker.read_text())['image_id']
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
                raise ValueError('Invalid tracked image ID')
            present = {value.removeprefix('sha256:') for value in
                       run(['docker', 'image', 'ls', '-q', '--no-trunc']).split()}
            if image.removeprefix('sha256:') in present:
                run(['docker', 'image', 'rm', image], timeout=120)
            marker.unlink()
        for name in ('current-images.json', 'kernel-builder.json'):
            (assets / name).unlink(missing_ok=True)
    if cleanup.get('guest_assets'):
        protected = [Path(p).resolve() for p in (machine.get('disk'), machine.get('kernel')) if p]
        excluded = [supervisor, *(owned if cleanup.get('experiment_data') else [])]
        protected += list(protected_mounts(containers, root, excluded))
        for path in [*assets.glob('ball-catching-*.img'), *assets.glob('kernel-*')]:
            if path.is_symlink() or not (path.suffix == '.img' or re.fullmatch(r'kernel-[0-9a-f]{64}', path.name)):
                continue
            if any(p == path.resolve() or p.is_relative_to(path.resolve()) or path.resolve().is_relative_to(p) for p in protected):
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        # Current asset manifests are validated/rebuilt by the preparation pipeline.
        for name in ('current-guest.json', 'current-kernel.json'):
            (assets / name).unlink(missing_ok=True)
    return True
