"""Small durable building blocks shared by the controller and SSH supervisor."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import uuid


def run(argv, **kwargs):
    result = subprocess.run([str(x) for x in argv], capture_output=True, **kwargs)
    if result.returncode:
        error = result.stderr
        if isinstance(error, bytes):
            error = error.decode(errors='replace')
        raise RuntimeError((error or f'Command exited {result.returncode}')[-4000:])
    return result.stdout


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}', value):
        raise ValueError('Invalid identifier')
    return value


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def source_identity(root):
    git = ['git', '-c', 'safe.directory=' + str(root), '-C', root]
    revision = run([*git, 'rev-parse', 'HEAD'], text=True).strip()
    paths = ['gem5', 'gem5-transport', 'ros', 'containers', 'orchestrator/backend']
    diff = run([*git, 'diff', '--binary', 'HEAD', '--', *paths])
    extra = run([*git, 'ls-files', '--others', '--exclude-standard', '-z', '--', *paths])
    result = hashlib.sha256(revision.encode() + diff)
    for name in sorted(extra.decode().split('\0')):
        path = Path(root) / name
        if name and path.is_file():
            result.update(name.encode() + digest(path).encode())
    return dict(revision=revision, fingerprint=result.hexdigest())


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def tail_file(path, limit=100000):
    with Path(path).open('rb') as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read().decode(errors='replace')


def case_log(output, stream):
    if stream not in {'launch', 'guest', 'gem5'}:
        raise ValueError('Unknown log stream')
    attempts = sorted((Path(output) / 'suite').glob('case-*-attempt-*'))
    if not attempts:
        return 'No simulation logs yet.'
    relative = {'launch': 'launch.log', 'guest': 'gem5/board.pc.com_1.device', 'gem5': 'gem5/stats.txt'}[stream]
    path = attempts[-1] / relative
    return tail_file(path) if path.is_file() else 'This log has not been created yet.'


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')
        self.db.commit()

    def get(self, kind, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, kind, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
                            (kind, key, json.dumps(value)))

    def all(self, kind):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT body FROM records WHERE kind=? ORDER BY rowid', (kind,))]

    def delete(self, kind, key):
        with self.lock, self.db:
            self.db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, key))


def event(store, message, level='info', notify=False):
    key = str(time.time_ns())
    value = dict(id=key, time=time.time(), message=message, level=level, notify=notify, sent=False)
    store.put('events', key, value)
    return value
