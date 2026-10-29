"""Transport-independent draft v0.1 envelope and adapter contract."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Protocol
from uuid import UUID, uuid4

MAX_MESSAGE_BYTES = 1_048_576
KINDS = {"request", "response", "event", "cancel", "error"}
FIELDS = {"version", "id", "sender", "recipient", "kind", "conversation_id",
          "correlation_id", "created_at", "expires_at", "payload"}


class ProtocolError(ValueError):
    """The envelope violates the draft protocol."""


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ProtocolError("timestamp must be a string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError("invalid timestamp") from exc
    if result.tzinfo is None:
        raise ProtocolError("timestamp must include a timezone")
    return result


def identity(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ProtocolError("identity must be a nonempty string of at most 256 characters")


def uuid(value: str) -> None:
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProtocolError("IDs must be canonical UUID strings") from exc


def reject_constant(value: str):
    raise ProtocolError(f"non-JSON numeric constant: {value}")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def json_value(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("JSON object keys must be strings")
            json_value(item)
    elif isinstance(value, list):
        for item in value:
            json_value(item)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ProtocolError("payload must contain only JSON values")


class Message:
    """Validated envelope stored as immutable JSON; data access returns a copy."""

    def __init__(self, data: dict):
        if not isinstance(data, dict) or set(data) != FIELDS:
            raise ProtocolError("envelope must contain exactly the v0.1 fields")
        if data["version"] != "0.1":
            raise ProtocolError("unsupported protocol version")
        for key in ("id", "conversation_id"):
            uuid(data[key])
        if data["correlation_id"] is not None:
            uuid(data["correlation_id"])
        for key in ("sender", "recipient"):
            identity(data[key])
        if data["sender"] == "*":
            raise ProtocolError("sender cannot be the broadcast address")
        if not isinstance(data["kind"], str) or data["kind"] not in KINDS:
            raise ProtocolError("unsupported message kind")
        created = timestamp(data["created_at"])
        if data["expires_at"] is not None and timestamp(data["expires_at"]) <= created:
            raise ProtocolError("expires_at must be later than created_at")
        if not isinstance(data["payload"], dict):
            raise ProtocolError("payload must be a JSON object")
        try:
            json_value(data["payload"])
            self._json = json.dumps(data, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ProtocolError("payload must contain finite JSON values") from exc
        if len(self._json.encode()) > MAX_MESSAGE_BYTES:
            raise ProtocolError("message exceeds 1 MiB")

    @classmethod
    def create(cls, sender: str, recipient: str, payload: dict, *, kind="event",
               conversation_id=None, correlation_id=None, expires_at=None) -> Message:
        return cls(dict(version="0.1", id=str(uuid4()), sender=sender,
                        recipient=recipient, kind=kind,
                        conversation_id=conversation_id or str(uuid4()),
                        correlation_id=correlation_id,
                        created_at=datetime.now(timezone.utc).isoformat(),
                        expires_at=expires_at, payload=payload))

    @classmethod
    def from_json(cls, raw: str) -> Message:
        if not isinstance(raw, str) or len(raw.encode()) > MAX_MESSAGE_BYTES:
            raise ProtocolError("message must be UTF-8 JSON of at most 1 MiB")
        try:
            return cls(json.loads(raw, parse_constant=reject_constant,
                                  object_pairs_hook=unique_keys))
        except (ValueError, TypeError, RecursionError) as exc:
            raise ProtocolError(str(exc)) from exc

    @property
    def data(self) -> dict:
        return json.loads(self._json)

    @property
    def id(self) -> str:
        return self.data["id"]

    def to_json(self) -> str:
        return self._json

    def expired(self) -> bool:
        expiry = self.data["expires_at"]
        return expiry is not None and timestamp(expiry) <= datetime.now(timezone.utc)

    def reply(self, sender: str, payload: dict, *, kind="response") -> Message:
        data = self.data
        if data["recipient"] not in (sender, "*"):
            raise ProtocolError("reply sender must be a recipient of the original message")
        return Message.create(sender, data["sender"], payload, kind=kind,
                              conversation_id=data["conversation_id"], correlation_id=self.id)


class Surface(Protocol):
    """Adapters preserve envelopes and expose recipient-local acknowledgments."""

    def publish(self, message: Message) -> str: ...
    def receive(self, recipient: str, *, limit: int = 100) -> list[Message]: ...
    def acknowledge(self, recipient: str, message_id: str) -> None: ...
