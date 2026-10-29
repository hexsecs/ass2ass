"""Experimental POSIX, point-to-point NDJSON pipes. No durable queue."""
from __future__ import annotations

import errno
import math
import os
from pathlib import Path
import select
import stat
import time

from .protocol import MAX_MESSAGE_BYTES, Message, ProtocolError, identity, uuid


class PipeClosed(OSError):
    """Peer closed, or a partial write made the session unusable."""


class PipeSurface:
    """One reader/writer per direction, one identity per endpoint, no threads.

    Descriptors become nonblocking and belong to this object. Session history is
    bounded by max_messages; start a fresh session when that budget is exhausted.
    Reconnect/replay and durable deduplication belong to the caller.
    """

    def __init__(self, read_fd, write_fd, recipient, peer, *, timeout=5.0,
                 max_messages=10000, max_history_bytes=16 * 1024 * 1024,
                 wait_for_writer=False):
        if os.name != "posix":
            raise OSError("experimental pipes require POSIX")
        for name in (recipient, peer):
            identity(name)
            if name == "*":
                raise ProtocolError("pipes require explicit endpoint identities")
        if recipient == peer:
            raise ProtocolError("endpoint identities must differ")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if type(max_messages) is not int or max_messages < 1:
            raise ValueError("max_messages must be a positive integer")
        if type(max_history_bytes) is not int or max_history_bytes < 1:
            raise ValueError("max_history_bytes must be a positive integer")
        for fd in (read_fd, write_fd):
            if not stat.S_ISFIFO(os.fstat(fd).st_mode):
                raise OSError("descriptors must be POSIX pipes or FIFOs")
        self.read_fd, self.write_fd = read_fd, write_fd
        self.recipient, self.peer = recipient, peer
        self.timeout, self.max_messages = timeout, max_messages
        self.max_history_bytes = max_history_bytes
        self._incoming_bytes = self._outgoing_bytes = 0
        self._buffer = bytearray()
        self._incoming, self._outgoing = {}, {}
        self._pending, self._handled, self._receipts = {}, set(), set()
        self._saw_data = False
        self._wait_for_writer = wait_for_writer
        self._closed = self._broken = False
        self.bytes_written = self.write_wait_seconds = 0
        self.peak_buffer_bytes = 0
        for fd in (read_fd, write_fd):
            os.set_blocking(fd, False)

    @classmethod
    def open_fifos(cls, read_path, write_path, recipient, peer, *, timeout=5.0,
                   max_messages=10000, max_history_bytes=16 * 1024 * 1024):
        """Open two existing FIFOs; caller owns creation/removal of their paths."""
        if os.name != "posix":
            raise OSError("experimental FIFOs require POSIX")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if Path(read_path).resolve() == Path(write_path).resolve():
            raise ValueError("use a different FIFO for each direction")
        fds = []
        deadline = time.monotonic() + timeout
        flags = os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        try:
            # Open the reader first on BOTH ends to avoid a two-writer deadlock.
            paths = [os.lstat(path) for path in (read_path, write_path)]
            for info in paths:
                if not stat.S_ISFIFO(info.st_mode):
                    raise OSError("paths must be existing FIFOs, not symlinks")
            if (paths[0].st_dev, paths[0].st_ino) == (paths[1].st_dev, paths[1].st_ino):
                raise ValueError("use a distinct FIFO inode for each direction")
            fds.append(os.open(read_path, os.O_RDONLY | flags))
            while True:
                try:
                    fds.append(os.open(write_path, os.O_WRONLY | flags))
                    break
                except OSError as exc:
                    if exc.errno != errno.ENXIO:
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("timed out opening peer FIFO") from exc
                    time.sleep(min(0.01, remaining))
            return cls(*fds, recipient, peer, timeout=timeout,
                       max_messages=max_messages, max_history_bytes=max_history_bytes,
                       wait_for_writer=True)
        except BaseException:
            for fd in fds:
                os.close(fd)
            raise

    def _check(self):
        if self._closed or self._broken:
            raise PipeClosed("pipe session is closed or unusable; reconnect explicitly")

    def _remaining(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("pipe operation timed out")
        return remaining

    def _frame(self):
        self._check()
        deadline = time.monotonic() + self.timeout
        while True:
            split = self._buffer.find(b"\n")
            if split >= 0:
                raw = bytes(self._buffer[:split])
                del self._buffer[:split + 1]
                try:
                    return Message.from_json(raw.decode("utf-8"))
                except (UnicodeDecodeError, ProtocolError) as exc:
                    self._broken = True
                    raise ProtocolError(f"invalid pipe frame: {exc}") from exc
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                self._broken = True
                raise ProtocolError("pipe frame exceeds 1 MiB")
            ready, _, _ = select.select([self.read_fd], [], [], self._remaining(deadline))
            if not ready:
                raise TimeoutError("pipe read timed out")
            try:
                chunk = os.read(self.read_fd, min(65536, MAX_MESSAGE_BYTES + 1 - len(self._buffer)))
            except BlockingIOError:
                continue
            if not chunk:
                if self._saw_data or self._buffer or not self._wait_for_writer:
                    self._broken = True
                    if self._buffer:
                        raise ProtocolError("EOF in a partial pipe frame")
                    raise PipeClosed("peer closed its pipe")
                # A FIFO can report EOF before its first writer has connected.
                time.sleep(min(0.01, self._remaining(deadline)))
                continue
            self._saw_data = True
            self._buffer.extend(chunk)
            self.peak_buffer_bytes = max(self.peak_buffer_bytes, len(self._buffer))

    def publish(self, message):
        self._check()
        data = message.data
        if data["sender"] != self.recipient or data["recipient"] != self.peer:
            raise ProtocolError("pipe publication must match both endpoint identities")
        previous = self._outgoing.get(message.id)
        if previous is not None:
            if previous != message.to_json():
                raise ProtocolError("message ID already exists with different content")
            return message.id
        if len(self._outgoing) >= self.max_messages:
            raise ProtocolError("outgoing session history limit reached")
        frame = (message.to_json() + "\n").encode("utf-8")
        if self._outgoing_bytes + len(frame) - 1 > self.max_history_bytes:
            raise ProtocolError("outgoing session byte budget reached")
        view = memoryview(frame)
        deadline = time.monotonic() + self.timeout
        try:
            while view:
                self._remaining(deadline)
                try:
                    written = os.write(self.write_fd, view)
                    if not written:
                        raise PipeClosed("zero-byte pipe write")
                    self.bytes_written += written
                    view = view[written:]
                except BlockingIOError:
                    started = time.monotonic()
                    _, ready, _ = select.select([], [self.write_fd], [], self._remaining(deadline))
                    self.write_wait_seconds += time.monotonic() - started
                    if not ready:
                        raise TimeoutError("pipe write timed out under backpressure")
        except (OSError, TimeoutError):
            self._broken = True
            raise
        self._outgoing[message.id] = message.to_json()
        self._outgoing_bytes += len(frame) - 1
        return message.id

    @staticmethod
    def _is_receipt(message):
        return (message.data["kind"] == "event" and
                message.data["payload"].get("type") == "receipt")

    def receive(self, recipient, *, limit=100):
        self._check()
        if recipient != self.recipient:
            raise ProtocolError("receive identity must match this endpoint")
        if type(limit) is not int or limit < 1:
            raise ProtocolError("limit must be a positive integer")
        self._pending = {k: v for k, v in self._pending.items() if not v.expired()}
        if self._pending:
            return list(self._pending.values())[:limit]
        message = self._frame()
        data = message.data
        if data["sender"] != self.peer or data["recipient"] != self.recipient:
            self._broken = True
            raise ProtocolError("incoming envelope does not match pipe endpoints")
        old = self._incoming.get(message.id)
        if old is not None:
            if old != message.to_json():
                self._broken = True
                raise ProtocolError("incoming message ID collision")
            return []
        if len(self._incoming) >= self.max_messages:
            self._broken = True
            raise ProtocolError("incoming session history limit reached")
        size = len(message.to_json().encode("utf-8"))
        if self._incoming_bytes + size > self.max_history_bytes:
            self._broken = True
            raise ProtocolError("incoming session byte budget reached")
        self._incoming[message.id] = message.to_json()
        self._incoming_bytes += size
        if message.expired():
            return []
        if self._is_receipt(message):
            original = self._outgoing.get(data["correlation_id"])
            if (original is None or data["payload"].get("status") != "handled"
                    or Message.from_json(original).data["conversation_id"] != data["conversation_id"]
                    or self._is_receipt(Message.from_json(original))):
                self._broken = True
                raise ProtocolError("invalid or unsolicited pipe receipt")
            self._receipts.add(data["correlation_id"])
            return []
        self._pending[message.id] = message
        return [message]

    def acknowledge(self, recipient, message_id):
        self._check()
        uuid(message_id)
        if recipient != self.recipient:
            raise ProtocolError("cannot acknowledge another endpoint's messages")
        if message_id in self._handled:
            return
        if message_id not in self._pending:
            raise ProtocolError("can only acknowledge a received, pending message")
        original = self._pending[message_id]
        receipt = original.reply(self.recipient, {"type": "receipt", "status": "handled"}, kind="event")
        self.publish(receipt)
        self._handled.add(message_id)
        del self._pending[message_id]

    def was_acknowledged(self, message_id):
        uuid(message_id)
        return message_id in self._receipts

    def close(self):
        if not self._closed:
            self._closed = True
            for fd in {self.read_fd, self.write_fd}:
                os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
