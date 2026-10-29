"""Reference adapter for a trusted shared local filesystem."""
from hashlib import sha256
import os
from pathlib import Path
import tempfile

from .protocol import MAX_MESSAGE_BYTES, Message, ProtocolError, identity, uuid


class DirectorySurface:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.messages = self.root / "messages"
        self.receipts = self.root / "receipts"
        self.messages.mkdir(parents=True, exist_ok=True)
        self.receipts.mkdir(parents=True, exist_ok=True)

    def _read(self, path: Path) -> Message:
        with path.open("rb") as stream:
            raw = stream.read(MAX_MESSAGE_BYTES + 1)
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ProtocolError(f"oversized message: {path.name}")
        try:
            message = Message.from_json(raw.decode("utf-8"))
        except (UnicodeDecodeError, ProtocolError) as exc:
            raise ProtocolError(f"invalid message {path.name}: {exc}") from exc
        if path.stem != message.id:
            raise ProtocolError(f"message ID does not match filename: {path.name}")
        return message

    def publish(self, message: Message) -> str:
        target = self.messages / f"{message.id}.json"
        # Hard-link publication is atomic and never overwrites another writer.
        # Temporary file lives on the same filesystem as the final envelope.
        fd, temp = tempfile.mkstemp(prefix=".pending-", dir=self.messages)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(message.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp, target)
            except FileExistsError:
                if self._read(target).to_json() != message.to_json():
                    raise ProtocolError("message ID already exists with different content")
        finally:
            os.unlink(temp)
        return message.id

    def _receipt(self, recipient: str, message_id: str) -> Path:
        identity(recipient)
        if recipient == "*":
            raise ProtocolError("use an individual recipient for receipts")
        uuid(message_id)
        return self.receipts / sha256(recipient.encode()).hexdigest() / message_id

    def receive(self, recipient: str, *, limit: int = 100) -> list[Message]:
        self._receipt(recipient, "00000000-0000-0000-0000-000000000000")
        if type(limit) is not int or limit < 1:
            raise ProtocolError("limit must be a positive integer")
        result = []
        for path in sorted(self.messages.glob("*.json")):
            # Validate before using any file-derived identifier in another path.
            message = self._read(path)
            if (message.data["recipient"] in (recipient, "*") and not message.expired()
                    and not self._receipt(recipient, message.id).exists()):
                result.append(message)
                if len(result) >= limit:
                    break
        return result

    def acknowledge(self, recipient: str, message_id: str) -> None:
        receipt = self._receipt(recipient, message_id)
        message = self._read(self.messages / f"{message_id}.json")
        if message.data["recipient"] not in (recipient, "*"):
            raise ProtocolError("cannot acknowledge another recipient's message")
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.touch(exist_ok=True)
