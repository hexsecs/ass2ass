from concurrent.futures import ThreadPoolExecutor
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ass2ass import DirectorySurface, Message, ProtocolError
from ass2ass.mount_probe import observe, probe


class FilesystemHardening(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.surface = DirectorySurface(self.root/'shared')
        self.message = Message.create('planner', 'builder', {'task': 'persist this'})

    def test_consumers_can_both_observe_before_receipt(self):
        self.surface.publish(self.message)
        barrier = threading.Barrier(2)
        def consume():
            endpoint = DirectorySurface(self.surface.root)
            found = endpoint.receive('builder')
            barrier.wait(timeout=2)
            endpoint.acknowledge('builder', self.message.id)
            return [m.id for m in found]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: consume(), range(2)))
        self.assertEqual(results, [[self.message.id], [self.message.id]])
        self.assertEqual(self.surface.receive('builder'), [])

    def test_restart_persists_unhandled_messages_and_receipts(self):
        self.surface.publish(self.message)
        script = ('import sys; from ass2ass import DirectorySurface; '
                  's=DirectorySurface(sys.argv[1]); m=s.receive("builder")[0]; '
                  'print(m.id); s.acknowledge("builder",m.id)')
        child = subprocess.run([sys.executable, '-c', script, str(self.surface.root)],
                               text=True, capture_output=True, check=True, timeout=5)
        self.assertEqual(child.stdout.strip(), self.message.id)
        self.assertEqual(DirectorySurface(self.surface.root).receive('builder'), [])
        self.assertTrue((self.surface.messages/f'{self.message.id}.json').exists())

    def test_fsync_and_link_failures_leave_no_published_envelope(self):
        for operation in ('fsync', 'link'):
            with self.subTest(operation=operation):
                with patch('ass2ass.filesystem.os.'+operation, side_effect=OSError(errno.EIO, 'injected failure')):
                    with self.assertRaises(OSError):
                        self.surface.publish(self.message)
                self.assertEqual(list(self.surface.messages.iterdir()), [])

    def test_cleanup_failure_after_publication_has_ambiguous_outcome(self):
        with patch('ass2ass.filesystem.os.unlink', side_effect=OSError('injected cleanup failure')):
            with self.assertRaises(OSError):
                self.surface.publish(self.message)
        # Publication completed despite the error: reconcile/retry the same ID.
        self.assertEqual(self.surface.receive('builder')[0].data, self.message.data)
        pending = list(self.surface.messages.glob('.pending-*'))
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.surface.publish(self.message), self.message.id)
        self.assertTrue(pending[0].exists())  # A retry never deletes another attempt's scratch.

    def test_crashed_writer_before_and_after_link(self):
        for linked in (False, True):
            with self.subTest(linked=linked):
                root = self.root/str(linked)
                script = '''import os,sys,tempfile
from ass2ass import DirectorySurface,Message
s=DirectorySurface(sys.argv[1]); m=Message.from_json(sys.argv[2])
fd,p=tempfile.mkstemp(prefix='.pending-',dir=s.messages)
with os.fdopen(fd,'w') as f:
 f.write(m.to_json()); f.flush(); os.fsync(f.fileno())
if sys.argv[3]=='yes': os.link(p,s.messages/(m.id+'.json'))
os._exit(23)
'''
                child = subprocess.run([sys.executable, '-c', script, str(root), self.message.to_json(),
                                        'yes' if linked else 'no'], timeout=5)
                self.assertEqual(child.returncode, 23)
                endpoint = DirectorySurface(root)
                self.assertEqual([m.id for m in endpoint.receive('builder')], [self.message.id] if linked else [])
                self.assertEqual(len(list(endpoint.messages.glob('.pending-*'))), 1)

    def test_malformed_records_and_history_are_never_automatically_cleaned(self):
        self.surface.publish(self.message)
        self.surface.acknowledge('builder', self.message.id)
        bad = self.surface.messages/'invalid.json'
        bad.write_text('{not json')
        before = {p: p.read_bytes() for p in self.surface.root.rglob('*') if p.is_file()}
        with self.assertRaises(ProtocolError):
            self.surface.receive('builder')
        self.assertEqual(before, {p: p.read_bytes() for p in self.surface.root.rglob('*') if p.is_file()})

    def test_probe_observations_and_shared_history_preserved(self):
        self.surface.publish(self.message)
        before = {p: p.read_bytes() for p in self.surface.root.rglob('*') if p.is_file()}
        result = probe(self.root)
        self.assertEqual(result['status'], 'observed')
        self.assertTrue(result['scratch_removed'])
        self.assertIn('receipt_visible_after_worker_exit', result['checks'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.surface.root.rglob('*') if p.is_file()})
        self.assertEqual(list(self.root.glob('.ass2ass-probe-*')), [])

    def test_unsupported_hardlinks_report_failure(self):
        root = self.root/'.ass2ass-probe-test'
        root.mkdir()
        with patch('ass2ass.mount_probe.os.link', side_effect=OSError(errno.ENOTSUP, 'unsupported')):
            result = observe(root)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errno'], errno.ENOTSUP)

    def test_probe_requires_existing_path_and_valid_deadline(self):
        for value in (0, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                probe(self.root, value)
        with self.assertRaises(FileNotFoundError):
            probe(self.root/'missing')

    def test_probe_deadline_reaps_worker_and_removes_only_scratch(self):
        real_popen = subprocess.Popen
        def stalled(command, **kwargs):
            return real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        with patch('ass2ass.mount_probe.subprocess.Popen', side_effect=stalled):
            result = probe(self.root, timeout=.05)
        self.assertEqual(result['status'], 'timeout')
        self.assertTrue(result['scratch_removed'])
        self.assertEqual(list(self.root.glob('.ass2ass-probe-*')), [])
        self.assertTrue(self.surface.root.exists())

    def test_probe_reports_cleanup_failure_and_retained_scratch(self):
        with patch('ass2ass.mount_probe.shutil.rmtree', side_effect=PermissionError('injected cleanup failure')):
            result = probe(self.root)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['scratch_removed'])
        self.assertIn('cleanup_error', result)
        self.assertTrue(Path(result['scratch_directory']).exists())


if __name__ == '__main__':
    unittest.main()
