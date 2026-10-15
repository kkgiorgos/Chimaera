"""Internal HTTP API and persistent scheduler, isolated behind the Next.js proxy."""
import fcntl
import json
import os
import shutil
from pathlib import Path
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request

from common import atomic_json, event, identifier, case_log, tail_file
from controller import Controller, estimates, validate_config
from reports import build_report


def notify(controller):
    webhook = os.environ.get('DISCORD_WEBHOOK_URL', '')
    if not webhook:
        return
    for item in controller.store.all('events'):
        if not item['notify'] or item['sent']:
            continue
        try:
            request = urllib.request.Request(webhook, data=json.dumps(dict(content=item['message'][:1900],
                allowed_mentions=dict(parse=[]))).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'Chimaera/1.0'})
            with urllib.request.urlopen(request, timeout=10):
                pass
            item['sent'] = True
            controller.store.put('events', item['id'], item)
        except Exception:
            # Retain the outbox through restart and retry transport delivery, never benchmark cases.
            break


def periodic(controller):
    settings = controller.store.get('settings', 'main')
    if not settings['progress_enabled'] or time.time() - settings['last_progress'] < settings['progress_interval']:
        return
    active = next((e for e in controller.store.all('experiments') if e['status'] in ('preparing', 'running', 'needs_attention', 'stopping')), None)
    if not active:
        return
    if time.time() - max(settings['last_progress'], active.get('activated', 0)) < settings['progress_interval']:
        return
    active = estimates(active, controller.store.all('machines'))
    completed = sum(c['status'] == 'completed' for c in active['cases'])
    eta = active.get('eta_seconds')
    remaining = f'{eta / 3600:.1f} hours estimated remaining' if eta is not None else 'ETA awaiting sufficient data'
    event(controller.store, f"{active['name']}: {completed}/{len(active['cases'])} complete; {active['status']}; {remaining}", notify=True)
    settings['last_progress'] = time.time()
    controller.store.put('settings', 'main', settings)


def serve(controller, port=8080):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, value, code=200):
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                parts = self.path.split('?', 1)[0].strip('/').split('/')
                if parts == ['state']:
                    state = controller.state()
                    state['notifications_configured'] = bool(os.environ.get('DISCORD_WEBHOOK_URL'))
                    self.respond(state)
                elif parts == ['defaults']:
                    source = controller.root / 'ros/ball_catching_ws/experiments/memory_contention_patterns.json'
                    config = json.loads(source.read_text())
                    config['ramulator_config'] = str((source.parent / config['ramulator_config']).resolve().relative_to(controller.root))
                    self.respond(dict(config=config))
                elif parts[0] == 'logs' and len(parts) in (3, 4):
                    machine = controller.machine(parts[1])
                    task = identifier(parts[2])
                    stream = parts[3] if len(parts) == 4 else 'worker'
                    local = next((controller.data / 'results' / e['id'] / 'attempts' / task
                        for e in controller.store.all('experiments')
                        if any(a['id'] == task and a['machine'] == machine['id'] and a.get('collected') for c in e['cases'] for a in c['attempts'])), None)
                    if local and local.is_dir():
                        log = tail_file(local / 'worker.log') if stream == 'worker' else case_log(local, stream)
                    else:
                        log = controller.remote.call(machine, 'logs', task=task, stream=stream)
                    self.respond(dict(log=log))
                elif parts[0] in ('download', 'report') and len(parts) == 2:
                    experiment = controller.experiment(parts[1])
                    if experiment.get('results_deleted'):
                        raise ValueError('Collected results have been deleted')
                    base = controller.data / 'results' / experiment['id']
                    atomic_json(base / 'run.json', experiment)
                    if parts[0] == 'report':
                        path = build_report(controller, experiment)
                        self.send_file(path, 'text/html')
                    else:
                        if any(c['status'] == 'completed' for c in experiment['cases']):
                            build_report(controller, experiment)
                        # Stream a fresh archive; include all collected attempts, config and provenance.
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/gzip')
                        self.send_header('Content-Disposition', f'attachment; filename="chimaera-{experiment["id"]}.tar.gz"')
                        self.end_headers()
                        with tarfile.open(fileobj=self.wfile, mode='w|gz') as archive:
                            archive.add(base, arcname=experiment['id'], recursive=True,
                                filter=lambda item: None if '.incoming' in Path(item.name).parts or item.name.endswith(('.part', '.tmp')) else item)
                else:
                    self.respond(dict(error='Not found'), 404)
            except Exception as error:
                self.respond(dict(error=str(error)), 400)

        def send_file(self, path, content_type):
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(path.stat().st_size))
            self.end_headers()
            with path.open('rb') as stream:
                shutil.copyfileobj(stream, self.wfile)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2 * 1024**2:
                    raise ValueError('Request must be between 1 byte and 2 MiB')
                body = json.loads(self.rfile.read(length))
                if self.path == '/validate':
                    config, points = validate_config(controller.root, body['config'])
                    self.respond(dict(config=config, points=points))
                elif self.path == '/command':
                    self.respond(dict(id=controller.command(body['kind'], body['body'])), 202)
                else:
                    self.respond(dict(error='Not found'), 404)
            except Exception as error:
                self.respond(dict(error=str(error)), 400)

        def log_message(self, *_):
            pass

    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


def main():
    root = Path(os.environ.get('CHIMAERA_REPO', '/repo'))
    data = Path(os.environ.get('CHIMAERA_DATA', '/data'))
    data.mkdir(parents=True, exist_ok=True)
    lock = (data / 'scheduler.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    controller = Controller(root, data)

    def scheduler():
        while True:
            try:
                controller.tick()
                periodic(controller)
            except Exception as error:
                event(controller.store, 'Scheduler: ' + str(error), 'error')
            time.sleep(1)

    def notifications():
        while True:
            notify(controller)
            time.sleep(5)

    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=notifications, daemon=True).start()
    serve(controller)


if __name__ == '__main__':
    main()
