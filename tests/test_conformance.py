"""Shared protocol/adapter conformance, reusable by new adapter factories."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest

from ass2ass import DirectorySurface, Message, ProtocolError
from ass2ass.pipes import PipeSurface

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / 'skills/ass2ass/references'
FIXTURES = json.loads((REFERENCES / 'fixtures-0.1.json').read_text())


class EnvelopeFixtures(unittest.TestCase):
    def test_protocol_fixtures(self):
        for case in FIXTURES['cases']:
            with self.subTest(case=case['name']):
                if not case['valid']:
                    with self.assertRaises(ProtocolError):
                        Message.from_json(case['raw'])
                else:
                    message = Message.from_json(case['raw'])
                    self.assertEqual(message.expired(), case['expired'])
                    self.assertEqual(Message.from_json(message.to_json()).data, message.data)

    def test_structural_schema(self):
        # A development-only dependency. The runtime still uses the stdlib.
        from jsonschema import Draft202012Validator
        schema = json.loads((REFERENCES / 'envelope-0.1.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for case in FIXTURES['cases']:
            if 'schema_valid' in case:
                with self.subTest(case=case['name']):
                    self.assertEqual(validator.is_valid(json.loads(case['raw'])), case['schema_valid'])


class SurfaceContract:
    """Subclass with unittest.TestCase and provide setUp assigning a and b.

    Both endpoints must use planner/builder, one fresh surface/session per test.
    No ordering, persistence, broadcast, network, or exactly-once assumptions are
    made here. Adapter-specific profiles need additional tests for those claims.
    """
    def request(self):
        return Message.from_json(FIXTURES['cases'][0]['raw'])

    def assert_completed_receipts(self, request, response):
        self.assertEqual(self.a.receive('planner'), [])
        self.assertEqual(self.b.receive('builder'), [])

    def test_round_trip_and_explicit_handled_receipts(self):
        request = self.request()
        self.assertEqual(self.a.publish(request), request.id)
        received = self.b.receive('builder')
        self.assertEqual([m.data for m in received], [request.data])
        self.assertEqual(self.b.receive('builder')[0].data, request.data)
        response = received[0].reply('builder', {'result': 'reviewed'})
        self.b.publish(response)
        self.b.acknowledge('builder', request.id)
        self.b.acknowledge('builder', request.id)
        opened = self.a.receive('planner')[0]
        self.assertEqual(opened.data['conversation_id'], request.data['conversation_id'])
        self.assertEqual(opened.data['correlation_id'], request.id)
        self.a.acknowledge('planner', response.id)
        self.assert_completed_receipts(request, response)

    def test_retry_and_conflicting_id_fixtures(self):
        for envelope in FIXTURES['sequences'][0]['messages']:
            self.a.publish(Message(envelope))
        self.assertEqual([m.id for m in self.b.receive('builder')], [self.request().id])
        collision = Message(FIXTURES['sequences'][1]['messages'][1])
        with self.assertRaises(ProtocolError):
            self.a.publish(collision)
        self.assertEqual(self.b.receive('builder')[0].data, self.request().data)

    def test_expiry_filter_and_receipt_ownership(self):
        expired = Message.from_json(next(c['raw'] for c in FIXTURES['cases'] if c['name'] == 'expired'))
        self.a.publish(expired)
        self.assertEqual(self.b.receive('builder'), [])
        message = Message.create('planner', 'builder', {'task': 'hello'})
        self.a.publish(message)
        self.assertEqual(self.b.receive('builder')[0].id, message.id)
        with self.assertRaises(ProtocolError):
            self.b.acknowledge('stranger', message.id)


class DirectoryConformance(SurfaceContract, unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.a = DirectorySurface(folder.name)
        self.b = DirectorySurface(folder.name)


@unittest.skipUnless(os.name == 'posix', 'POSIX pipe profile')
class PipeConformance(SurfaceContract, unittest.TestCase):
    def assert_completed_receipts(self, request, response):
        self.assertEqual(self.a.receive('planner'), [])
        self.assertTrue(self.a.was_acknowledged(request.id))
        self.assertEqual(self.b.receive('builder'), [])
        self.assertTrue(self.b.was_acknowledged(response.id))

    def setUp(self):
        ar, bw = os.pipe()
        br, aw = os.pipe()
        self.a = PipeSurface(ar, aw, 'planner', 'builder', timeout=1)
        self.b = PipeSurface(br, bw, 'builder', 'planner', timeout=1)
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)


@unittest.skipUnless(os.name == 'posix', 'POSIX FIFO profile')
class FIFOConformance(PipeConformance):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        p, q = Path(folder.name)/'requests', Path(folder.name)/'replies'
        os.mkfifo(p, 0o600)
        os.mkfifo(q, 0o600)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(PipeSurface.open_fifos, q, p, 'planner', 'builder', timeout=1)
            second = pool.submit(PipeSurface.open_fifos, p, q, 'builder', 'planner', timeout=1)
            self.a, self.b = first.result(timeout=2), second.result(timeout=2)
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)


if __name__ == '__main__':
    unittest.main()
