import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from common import Store, event
from notifications import content, notification_event
from server import notify, periodic


class NotificationsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / 'controller.sqlite')
        self.controller = MagicMock(store=self.store)
        self.experiment = dict(id='e1', name='Memory suite', status='running',
            activated=100, machines=[], cases=[dict(id='case-001', status='failed', attempts=[])])

    def test_failure_context_is_persisted(self):
        item = notification_event(self.store, 'original activity', 'error', title='Case failed',
            experiment=self.experiment, case=self.experiment['cases'][0],
            attempt=dict(id='a1', status='failed', started=100, finished=165, cpus=2,
                         memory='4g', exit_code=137, oom=True),
            machine=dict(id='m1', name='Worker 1'), detail='Simulation exited')
        body = content(self.store.get('events', item['id']))
        for expected in ('**Chimaera · Case failed**', 'Memory suite', 'case-001', 'Worker 1',
                         '1m 05s', '137', 'Memory limit exceeded', '0/1 completed', '1 failed'):
            self.assertIn(expected, body)
        self.assertEqual(item['message'], 'original activity')
        self.experiment['name'] = 'Renamed'
        self.assertEqual(content(self.store.get('events', item['id'])), body)

    def test_long_untrusted_text_is_bounded(self):
        self.experiment['name'] = '**@everyone**\n# heading ' * 1000
        item = notification_event(self.store, 'activity', title='Warning',
            experiment=self.experiment, detail='`' * 10000,
            machine=dict(id='m' * 500, name='w' * 500),
            case=dict(id='c' * 500, point=dict(label='p' * 500)),
            attempt=dict(id='x' * 500, status='failed', started=100, cpus=2, memory='4g',
                         exit_code=1, oom=True, progress=dict(phase='y' * 500, average_speed=0.1)))
        body = content(item)
        self.assertLessEqual(len(body), 1900)
        self.assertNotIn('\n# heading', body)
        self.assertIn('Additional details available', body)

    def test_delivery_retries_and_formats_legacy_events(self):
        item = event(self.store, 'old notification', notify=True)
        with patch.dict('os.environ', DISCORD_WEBHOOK_URL='https://example.invalid/webhook'), \
                patch('server.urllib.request.urlopen') as send:
            send.side_effect = OSError('offline')
            notify(self.controller)
            self.assertFalse(self.store.get('events', item['id'])['sent'])
            send.side_effect = None
            notify(self.controller)
            payload = json.loads(send.call_args.args[0].data)
            self.assertIn('**Chimaera · Info**', payload['content'])
            self.assertEqual(payload['allowed_mentions'], dict(parse=[]))
            self.assertTrue(self.store.get('events', item['id'])['sent'])
            notify(self.controller)
            self.assertEqual(send.call_count, 2)

    def test_periodic_snapshot_includes_eta_and_case_counts(self):
        self.store.put('settings', 'main', dict(progress_enabled=True, last_progress=0, progress_interval=60))
        self.store.put('experiments', 'e1', self.experiment)
        with patch('server.estimates', side_effect=lambda e, m: dict(e, eta_seconds=3600, eta_basis='Observed durations')):
            periodic(self.controller)
        body = content(self.store.all('events')[0])
        self.assertIn('Progress update', body)
        self.assertIn('1h 00m', body)
        self.assertIn('Observed durations', body)
        self.assertIn('1 failed', body)
