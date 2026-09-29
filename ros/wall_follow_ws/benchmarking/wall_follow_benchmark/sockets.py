"""Preflight Linux Chimaera Unix sockets without connecting to the data protocol."""
import os
from pathlib import Path
import stat

CHIMAERA_SOCKETS = ('/tmp/chimaera_g2h.sock', '/tmp/chimaera_h2g.sock', '/tmp/chimaera_time.sock')


def prepare_sockets(paths=CHIMAERA_SOCKETS):
    """Remove only this user's inactive socket files; never probe a live transport."""
    candidates = []
    for name in paths:
        path = Path(name)
        try:
            identity = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISSOCK(identity.st_mode) or identity.st_uid != os.getuid():
            raise RuntimeError(f'Refusing to remove non-socket or foreign-owned path: {path}')
        candidates.append((path, identity))
    # /proc lists bound Unix sockets even when they have not started listening.
    # Fail closed if the kernel table cannot be inspected.
    active = {parts[7] for line in Path('/proc/net/unix').read_text().splitlines()[1:]
              if len(parts := line.split(maxsplit=7)) == 8}
    if any(str(path) in active for path in paths):
        raise RuntimeError('Another Chimaera session owns a socket; stop it before running the suite')
    removed = []
    for path, identity in candidates:
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise RuntimeError(f'Socket changed during preflight: {path}')
        path.unlink()
        removed.append(str(path))
    return removed
