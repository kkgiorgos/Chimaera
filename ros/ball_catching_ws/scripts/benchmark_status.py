"""Local transport preflight and explicit co-simulation failure diagnostics."""
import json
import re
from pathlib import Path

LOCAL_TRANSPORT_PATHS = tuple(Path('/tmp/chimaera_' + side + '.sock')
                              for side in ('g2h', 'h2g', 'time'))


def check_local_transport():
    """Refuse occupied paths; never remove another session's sockets."""
    occupied = [str(path) for path in LOCAL_TRANSPORT_PATHS
                if path.exists() or path.is_symlink()]
    if occupied:
        raise RuntimeError('Local Chimaera socket paths already exist: ' + ', '.join(occupied)
            + '. Stop any active session; check ownership with ss -xapn and remove only unowned stale sockets.')


def explicit_failure(path, patterns):
    try:
        log = path.read_text(errors='replace')
    except FileNotFoundError:
        return None
    for pattern in patterns:
        match = re.search(pattern, log, re.MULTILINE)
        if match:
            return match.group().strip()
    return None


def guest_failure(path):
    return explicit_failure(path, (r'^Guest bridge: .+$',
        r'^\w+ exited \(-?\d+\); stopping session\r?$',
        r'^CHIMAERA_GUEST_APPLICATION_EXIT=\d+\r?$'))


def host_failure(path):
    return explicit_failure(path, (
        r'^\[[^\r\n]+\] Host bridge: .*\bsocket timeout\b[^\r\n]*\r?$',
        r'^\[[^\r\n]+\] .*Assertion .+ failed\.\r?$',
        r'^\[[^\r\n]+\] .*\b(?:panic|fatal): .+\r?$',
        r'^\[[^\r\n]+\] Host bridge: .+\r?$'))


def launch_failure(path):
    """A broken physical throw is an infrastructure failure, not a task miss."""
    try:
        validation = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if isinstance(validation, dict) and validation.get('valid') is False:
        return 'Invalid ball launch: ' + str(validation.get('reason', 'launch validation failed'))
    return None
