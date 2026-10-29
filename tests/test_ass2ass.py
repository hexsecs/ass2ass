from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ass2ass import DirectorySurface, Message, ProtocolError


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.surface = DirectorySurface(self.temp.name)

    def message(self, to="builder", **kw):
        return Message.create("planner", to, {"task": "add", "values": [17, 25]}, **kw)

    def test_request_reply_and_receipts(self):
        request = self.message(kind="request")
        self.surface.publish(request)
        self.assertEqual([m.id for m in self.surface.receive("builder")], [request.id])
        self.assertEqual(self.surface.receive("planner"), [])
        reply = request.reply("builder", {"result": 42})
        self.surface.publish(reply)
        self.assertEqual(reply.data["correlation_id"], request.id)
        self.assertEqual(reply.data["conversation_id"], request.data["conversation_id"])
        self.surface.acknowledge("builder", request.id)
        self.assertEqual(DirectorySurface(self.temp.name).receive("builder"), [])
        self.assertEqual(self.surface.receive("planner")[0].data["payload"], {"result": 42})

    def test_broadcast_receipt_is_independent(self):
        message = self.message("*")
        self.surface.publish(message)
        self.surface.acknowledge("builder", message.id)
        self.assertEqual(self.surface.receive("builder"), [])
        self.assertEqual(self.surface.receive("reviewer")[0].id, message.id)

    def test_repeated_delivery_until_ack(self):
        message = self.message()
        self.surface.publish(message)
        self.assertEqual(self.surface.receive("builder")[0].id, message.id)
        self.assertEqual(self.surface.receive("builder")[0].id, message.id)
        with self.assertRaises(ProtocolError):
            self.surface.acknowledge("stranger", message.id)

    def test_concurrent_same_message_publication(self):
        message = self.message()
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(self.surface.publish, [message] * 32)), [message.id] * 32)
        self.assertEqual(len(list(self.surface.messages.glob("*.json"))), 1)
        self.assertEqual(len(self.surface.receive("builder")), 1)
        self.assertEqual(list(self.surface.messages.glob(".pending-*")), [])

    def test_id_collision_is_rejected(self):
        message = self.message()
        self.surface.publish(message)
        data = message.data
        data["payload"] = {"different": True}
        with self.assertRaises(ProtocolError):
            self.surface.publish(Message(data))
        self.assertEqual(self.surface.receive("builder")[0].to_json(), message.to_json())

    def test_expired_message_is_not_delivered(self):
        data = self.message().data
        now = datetime.now(timezone.utc)
        data["created_at"] = (now - timedelta(hours=2)).isoformat()
        data["expires_at"] = (now - timedelta(hours=1)).isoformat()
        self.surface.publish(Message(data))
        self.assertEqual(self.surface.receive("builder"), [])

    def test_invalid_envelopes(self):
        base = self.message().data
        for key, value in [("version", "9"), ("id", "../../escape"),
                           ("kind", []), ("sender", "*"), ("payload", []),
                           ("created_at", "2026-10-29"), ("expires_at", "2020-01-01T00:00:00Z")]:
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                Message({**base, key: value})
        for payload in [{"x": float("nan")}, {"nested": {1: 2}}, {"x": (1, 2)}]:
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                Message.create("a", "b", payload)
        for raw in ['{"version":"0.1","version":"0.1"}', '[]', 'null', '{', '{"x":NaN}']:
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                Message.from_json(raw)

    def test_snapshot_is_immutable(self):
        payload = {"nested": [1]}
        message = Message.create("a", "b", payload)
        payload["nested"].append(2)
        message.data["payload"]["nested"].append(3)
        self.assertEqual(message.data["payload"], {"nested": [1]})

    def test_size_and_filename_validation(self):
        with self.assertRaises(ProtocolError):
            self.message().reply("builder", {"huge": "x" * 1_048_576})
        message = self.message()
        (self.surface.messages / "not-the-id.json").write_text(message.to_json())
        with self.assertRaises(ProtocolError):
            self.surface.receive("builder")

    def test_malformed_file_reports_error_without_deletion(self):
        path = self.surface.messages / "bad.json"
        path.write_text("{")
        with self.assertRaises(ProtocolError):
            self.surface.receive("builder")
        self.assertTrue(path.exists())

    def test_limits_and_identity_path_safety(self):
        message = self.message("../../someone")
        self.surface.publish(message)
        self.surface.acknowledge("../../someone", message.id)
        self.assertEqual(len(list(self.surface.receipts.iterdir())), 1)
        for value in [0, -1, True]:
            with self.assertRaises(ProtocolError):
                self.surface.receive("builder", limit=value)
        with self.assertRaises(ProtocolError):
            self.surface.acknowledge("builder", "../../escape")

    def test_cli_round_trip_and_error_exit(self):
        script = Path(__file__).resolve().parents[1] / "skills/ass2ass/scripts/ass2ass_cli.py"
        base = [sys.executable, str(script), "--surface", self.temp.name]
        sent = subprocess.run(base + ["send", "--from", "planner", "--to", "builder", "--payload", '{"task":"hello"}'], capture_output=True, text=True, check=True)
        message_id = json.loads(sent.stdout)["id"]
        received = subprocess.run(base + ["receive", "--as", "builder"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(received.stdout)["id"], message_id)
        subprocess.run(base + ["ack", "--as", "builder", message_id], capture_output=True, check=True)
        error = subprocess.run(base + ["send", "--from", "a", "--to", "b", "--payload", '[]'], capture_output=True, text=True)
        self.assertEqual(error.returncode, 1)
        self.assertIn("error", json.loads(error.stderr))


if __name__ == "__main__":
    unittest.main()
