from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from ass2ass import Message, ProtocolError
from ass2ass.pipe_cli import round_trip
from ass2ass.pipes import PipeClosed, PipeSurface
from ass2ass.protocol import MAX_MESSAGE_BYTES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
from pipe_round_trip import connected


@unittest.skipUnless(os.name == "posix", "experimental POSIX pipes only")
class PipeTests(unittest.TestCase):
    def pair(self, **kw):
        ar, bw = os.pipe()
        br, aw = os.pipe()
        a = PipeSurface(ar, aw, "a", "b", timeout=0.2, **kw)
        b = PipeSurface(br, bw, "b", "a", timeout=0.2, **kw)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        return a, b

    def test_two_process_modes_and_large_unicode_payload(self):
        for mode in ("stdio", "fifo"):
            with self.subTest(mode=mode), connected(mode, count=3) as surface:
                for payload in ({"hello": "world"}, {"large": "💌\n" * 10000}, {"again": True}):
                    response = round_trip(surface, payload)
                    self.assertEqual(response.data["payload"], {"echo": payload})

    def test_pending_until_ack_and_receipt_no_loop(self):
        a, b = self.pair()
        request = Message.create("a", "b", {"task": "hello"}, kind="request")
        a.publish(request)
        self.assertEqual(b.receive("b")[0].id, request.id)
        self.assertEqual(b.receive("b")[0].id, request.id)
        b.acknowledge("b", request.id)
        b.acknowledge("b", request.id)
        self.assertEqual(a.receive("a"), [])
        self.assertTrue(a.was_acknowledged(request.id))
        with self.assertRaises(TimeoutError):
            b.receive("b")  # a receipt never triggers a receipt of its own

    def test_partial_reads_multiple_frames_and_unescaped_utf8(self):
        a, b = self.pair()
        first = Message.create("a", "b", {"hello": "💌\n"})
        second = Message.create("a", "b", {"n": 2})
        wire = json.dumps(first.data, ensure_ascii=False).encode() + b"\n"
        split = wire.index("💌".encode()) + 1
        os.write(a.write_fd, wire[:split])
        with self.assertRaises(TimeoutError):
            b.receive("b")
        os.write(a.write_fd, wire[split:] + (second.to_json() + "\n").encode())
        self.assertEqual(b.receive("b")[0].data, first.data)
        b.acknowledge("b", first.id)
        self.assertEqual(b.receive("b")[0].id, second.id)

    def test_eof_and_partial_eof(self):
        for raw, error in [(b'{"partial":', ProtocolError),
                           ((Message.create("a", "b", {}).to_json() + "\n").encode(), PipeClosed)]:
            with self.subTest(raw=raw[:15]):
                a, b = self.pair()
                os.write(a.write_fd, raw)
                a.close()
                if error is PipeClosed:
                    message = b.receive("b")[0]
                    # Remove pending state without a receipt to the closed peer.
                    b._pending.pop(message.id)
                with self.assertRaises(error):
                    b.receive("b")

    def test_empty_peer_and_slow_reader_time_out(self):
        a, b = self.pair()
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            b.receive("b")
        huge = Message.create("a", "b", {"data": "x" * 200000})
        with self.assertRaises(TimeoutError):
            a.publish(huge)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertGreater(a.write_wait_seconds, 0)
        with self.assertRaises(PipeClosed):
            a.publish(huge)  # partial writes require a fresh connection

    def test_broken_write(self):
        a, b = self.pair()
        b.close()
        with self.assertRaises(BrokenPipeError):
            a.publish(Message.create("a", "b", {}))

    def test_invalid_oversized_and_truncated_frames(self):
        for raw in (b"not json\n", b"\xff\n", b"\n", b"x" * (MAX_MESSAGE_BYTES + 1)):
            with self.subTest(size=len(raw)):
                a, b = self.pair()
                def write_raw():
                    # Blocking producer in its own thread so a >pipe-capacity
                    # invalid frame exercises the bounded streaming reader.
                    os.set_blocking(a.write_fd, True)
                    view = memoryview(raw)
                    while view:
                        view = view[os.write(a.write_fd, view):]
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(write_raw)
                    with self.assertRaises(ProtocolError):
                        b.receive("b")
                    b.close()
                    try:
                        future.result(timeout=2)
                    except BrokenPipeError:
                        pass

    def test_addressing_expiry_and_receipt_validation(self):
        a, b = self.pair()
        for sender, recipient in (("wrong", "b"), ("a", "wrong"), ("a", "*")):
            with self.assertRaises(ProtocolError):
                a.publish(Message.create(sender, recipient, {}))
        with self.assertRaises(ProtocolError):
            b.receive("someone-else")
        data = Message.create("a", "b", {}).data
        now = datetime.now(timezone.utc)
        data.update(created_at=(now - timedelta(hours=2)).isoformat(),
                    expires_at=(now - timedelta(hours=1)).isoformat())
        a.publish(Message(data))
        self.assertEqual(b.receive("b"), [])
        forged = Message.create("a", "b", {"type": "receipt", "status": "handled"})
        a.publish(forged)
        with self.assertRaises(ProtocolError):
            b.receive("b")

    def test_incoming_wrong_peer_is_rejected(self):
        a, b = self.pair()
        raw = Message.create("stranger", "b", {}).to_json() + "\n"
        os.write(a.write_fd, raw.encode())
        with self.assertRaises(ProtocolError):
            b.receive("b")

    def test_retry_collision_and_session_limit(self):
        a, b = self.pair(max_messages=1)
        message = Message.create("a", "b", {})
        a.publish(message)
        count = a.bytes_written
        a.publish(message)
        self.assertEqual(a.bytes_written, count)
        self.assertEqual(b.receive("b")[0].id, message.id)
        with self.assertRaises(ProtocolError):
            a.publish(Message({**message.data, "payload": {"different": True}}))
        with self.assertRaises(ProtocolError):
            a.publish(Message.create("a", "b", {}))
        with self.assertRaises(ProtocolError):
            b.acknowledge("stranger", message.id)
        b.acknowledge("b", message.id)
        # Inject a wire duplicate, then a collision, bypassing sender checks.
        os.write(a.write_fd, (message.to_json() + "\n").encode())
        self.assertEqual(b.receive("b"), [])
        os.write(a.write_fd, (Message({**message.data, "payload": {"x": 1}}).to_json() + "\n").encode())
        with self.assertRaises(ProtocolError):
            b.receive("b")

    def test_fifo_startup_timeout_and_path_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            p, q = Path(folder) / "p", Path(folder) / "q"
            os.mkfifo(p)
            os.mkfifo(q)
            with self.assertRaises(TimeoutError):
                PipeSurface.open_fifos(p, q, "a", "b", timeout=0.05)
            with self.assertRaises(ValueError):
                PipeSurface.open_fifos(p, p, "a", "b")
            q.unlink()
            q.symlink_to(p)
            with self.assertRaises((OSError, ValueError)):
                PipeSurface.open_fifos(p, q, "a", "b")
            q.unlink()
            os.link(p, q)
            with self.assertRaises(ValueError):
                PipeSurface.open_fifos(p, q, "a", "b")
            q.unlink()
            q.write_text("not a pipe")
            with self.assertRaises(OSError):
                PipeSurface.open_fifos(p, q, "a", "b")

    def test_history_byte_budget(self):
        a, b = self.pair(max_history_bytes=1)
        message = Message.create("a", "b", {})
        with self.assertRaisesRegex(ProtocolError, "byte budget"):
            a.publish(message)
        self.assertEqual(a.bytes_written, 0)
        os.write(a.write_fd, (message.to_json() + "\n").encode())
        with self.assertRaisesRegex(ProtocolError, "byte budget"):
            b.receive("b")

    def test_invalid_configuration_and_cli_errors(self):
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                PipeSurface.open_fifos("missing", "also-missing", "a", "b", timeout=timeout)
        result = subprocess.run([sys.executable, "-m", "ass2ass.pipe_cli", "--as", "b", "--peer", "a"],
                                input="", capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("error", json.loads(result.stderr))


if __name__ == "__main__":
    unittest.main()
