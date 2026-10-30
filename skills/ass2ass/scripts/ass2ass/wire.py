"""Opt-in encodings and standard JOSE protection around unchanged envelopes."""
from __future__ import annotations

import base64
import json

from .protocol import MAX_MESSAGE_BYTES, Message, ProtocolError, identity, unique_keys, reject_constant

MAX_WIRE_BYTES = 3 * MAX_MESSAGE_BYTES
MODES = ("none", "signed", "encrypted", "signed-encrypted")
ENCODINGS = ("json", "cbor")


def _json(raw):
    return json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant)


def _compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _jose():
    try:
        from jwcrypto import jwk, jws, jwe
    except ImportError as exc:
        raise ProtocolError("optional protection requires: pip install 'ass2ass[protection]'") from exc
    return jwk, jws, jwe


def _cbor():
    try:
        import cbor2
    except ImportError as exc:
        raise ProtocolError("CBOR requires: pip install 'ass2ass[cbor]'") from exc
    return cbor2


def _check_cbor(raw):
    """Bound structure before library decoding; disallow semantic extensions.

    This profile validator is not a CBOR codec. Only bignum tags 2/3 are allowed;
    definite lengths and depth <=64 prevent attacker-declared allocation sizes.
    """
    def item(offset, depth=0):
        if depth > 64 or offset >= len(raw):
            raise ProtocolError("CBOR depth or length limit exceeded")
        first = raw[offset]
        major, extra = first >> 5, first & 31
        offset += 1
        if extra < 24:
            value = extra
        elif extra in (24, 25, 26, 27):
            size = 1 << (extra - 24)
            if offset + size > len(raw):
                raise ProtocolError("truncated CBOR length")
            value = int.from_bytes(raw[offset:offset + size], "big")
            offset += size
        else:
            raise ProtocolError("indefinite or reserved CBOR value")
        if major in (0, 1):
            return offset
        if major in (2, 3):
            if value > len(raw) - offset:
                raise ProtocolError("CBOR string exceeds remaining frame")
            return offset + value
        if major in (4, 5):
            count = value * (2 if major == 5 else 1)
            if count > len(raw) - offset:
                raise ProtocolError("CBOR collection exceeds remaining frame")
            for _ in range(count):
                offset = item(offset, depth + 1)
            return offset
        if major == 6 and value in (2, 3):
            if offset >= len(raw) or raw[offset] >> 5 != 2:
                raise ProtocolError("CBOR bignum must contain a byte string")
            return item(offset, depth + 1)
        if major == 7 and extra in (20, 21, 22, 25, 26, 27):
            return offset
        raise ProtocolError("CBOR value or semantic tag is outside the JSON envelope profile")

    if not raw or item(0) != len(raw):
        raise ProtocolError("CBOR must contain exactly one complete value")


def encode_message(message, encoding):
    if encoding == "json":
        return message.to_json().encode("utf-8")
    if encoding != "cbor":
        raise ProtocolError("unsupported message encoding")
    raw = _cbor().dumps(message.data, canonical=True)
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("encoded message exceeds 1 MiB")
    _check_cbor(raw)
    return raw


def decode_message(raw, encoding):
    if not isinstance(raw, bytes) or len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("encoded message exceeds 1 MiB")
    try:
        if encoding == "json":
            return Message.from_json(raw.decode("utf-8"))
        if encoding != "cbor":
            raise ProtocolError("unsupported message encoding")
        _check_cbor(raw)
        cbor = _cbor()
        data = cbor.loads(raw)
        message = Message(data)
        if cbor.dumps(data, canonical=True) != raw:
            raise ProtocolError("CBOR must be deterministic, without duplicate keys")
        return message
    except ProtocolError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise ProtocolError("invalid encoded envelope") from exc


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text):
    if not isinstance(text, str) or not text.isascii():
        raise ProtocolError("body must be base64url text")
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise ProtocolError("invalid base64url body") from exc
    if _b64(raw) != text:
        raise ProtocolError("body must use unpadded canonical base64url")
    return raw


class WireCodec:
    """Fixed peer policy: no sniffing, negotiation, downgrade, or key discovery."""

    def __init__(self, local, peer, *, encoding="json", protection="none",
                 signing_key=None, verification_key=None, encryption_key=None, decryption_key=None):
        for name in (local, peer):
            identity(name)
            if name == "*":
                raise ProtocolError("wire policies require explicit identities")
        if local == peer or encoding not in ENCODINGS or protection not in MODES:
            raise ProtocolError("invalid wire endpoint or profile")
        self.local, self.peer = local, peer
        self.encoding, self.protection = encoding, protection
        self.signing_key, self.verification_key = signing_key, verification_key
        self.encryption_key, self.decryption_key = encryption_key, decryption_key
        if encoding == "cbor":
            _cbor()
        if protection != "none":
            _jose()
        if protection not in ("signed", "signed-encrypted") and (signing_key is not None or verification_key is not None):
            raise ProtocolError("signing keys require a signed protection mode")
        if protection not in ("encrypted", "signed-encrypted") and (encryption_key is not None or decryption_key is not None):
            raise ProtocolError("encryption keys require an encrypted protection mode")

    def _key(self, key, kind, private=False):
        if key is None:
            raise ProtocolError(f"missing explicitly configured {kind} key")
        expected = ("OKP", "Ed25519") if kind in ("signing", "verification") else ("EC", "P-256")
        if (key.get("kty"), key.get("crv")) != expected or (private and not key.has_private):
            raise ProtocolError(f"invalid {kind} key type or missing private material")
        return key

    def _header(self, encrypted=False):
        header = {"alg": "ECDH-ES" if encrypted else "EdDSA",
                  "typ": "ass2ass+jwe" if encrypted else "ass2ass+jws",
                  "cty": "application/ass2ass+" + self.encoding,
                  "ass2ass_encoding": self.encoding, "ass2ass_protection": self.protection}
        if encrypted:
            header["enc"] = "A256GCM"
            if self.protection == "signed-encrypted":
                header["cty"] = "application/jose"
        return header

    def validate_duplex(self):
        if self.protection in ("signed", "signed-encrypted"):
            self._key(self.signing_key, "signing", True)
            self._key(self.verification_key, "verification")
        if self.protection in ("encrypted", "signed-encrypted"):
            self._key(self.encryption_key, "encryption")
            self._key(self.decryption_key, "decryption", True)

    def _sign(self, raw):
        _, jws, _ = _jose()
        signed = jws.JWS(raw)
        signed.allowed_algs = ["EdDSA"]
        signed.add_signature(self._key(self.signing_key, "signing", True), protected=self._header())
        return signed.serialize(compact=True).encode("ascii")

    def _verify(self, raw):
        _, jws, _ = _jose()
        token = raw.decode("ascii")
        if len(token.split(".")) != 3:
            raise ProtocolError("expected a compact JWS")
        signed = jws.JWS()
        signed.allowed_algs = ["EdDSA"]
        signed.deserialize(token)
        if signed.jose_header != self._header():
            raise ProtocolError("unexpected JWS protected header")
        signed.verify(self._key(self.verification_key, "verification"), alg="EdDSA")
        return signed.payload

    def _encrypt(self, raw):
        _, _, jwe = _jose()
        encrypted = jwe.JWE(raw, protected=self._header(True), algs=["ECDH-ES", "A256GCM"])
        encrypted.add_recipient(self._key(self.encryption_key, "encryption"))
        return encrypted.serialize(compact=True).encode("ascii")

    def _decrypt(self, raw):
        _, _, jwe = _jose()
        token = raw.decode("ascii")
        if len(token.split(".")) != 5:
            raise ProtocolError("expected a compact JWE")
        encrypted = jwe.JWE(algs=["ECDH-ES", "A256GCM"])
        encrypted.deserialize(token)
        header = dict(encrypted.jose_header)
        ephemeral = header.pop("epk", None)
        if (header != self._header(True) or not isinstance(ephemeral, dict)
                or set(ephemeral) != {"kty", "crv", "x", "y"}
                or ephemeral.get("kty") != "EC" or ephemeral.get("crv") != "P-256"):
            raise ProtocolError("unexpected JWE protected header")
        encrypted.decrypt(self._key(self.decryption_key, "decryption", True))
        return encrypted.payload

    def encode(self, message):
        data = message.data
        if data["sender"] != self.local or data["recipient"] != self.peer:
            raise ProtocolError("outgoing envelope does not match wire endpoints")
        if message.expired():
            raise ProtocolError("cannot seal an expired envelope")
        raw = encode_message(message, self.encoding)
        try:
            if self.protection in ("signed", "signed-encrypted"):
                raw = self._sign(raw)
            if self.protection in ("encrypted", "signed-encrypted"):
                raw = self._encrypt(raw)
        except ProtocolError:
            raise
        except Exception as exc:
            raise ProtocolError("message could not be sealed under the configured policy") from exc
        if self.encoding == "json" and self.protection == "none":
            return raw
        result = _compact({"wire": "ass2ass/0.1", "encoding": self.encoding,
                           "protection": self.protection, "body": _b64(raw)}).encode("ascii")
        if len(result) > MAX_WIRE_BYTES:
            raise ProtocolError("wrapped message exceeds 3 MiB")
        return result

    def decode(self, raw):
        if not isinstance(raw, bytes) or len(raw) > MAX_WIRE_BYTES:
            raise ProtocolError("wire record must be bytes of at most 3 MiB")
        try:
            if self.encoding == "json" and self.protection == "none":
                message = decode_message(raw, "json")
            else:
                wrapper = _json(raw.decode("ascii"))
                if (not isinstance(wrapper, dict) or set(wrapper) != {"wire", "encoding", "protection", "body"}
                        or wrapper["wire"] != "ass2ass/0.1" or wrapper["encoding"] != self.encoding
                        or wrapper["protection"] != self.protection):
                    raise ProtocolError("wire profile differs from required peer policy")
                body = _unb64(wrapper["body"])
                if self.protection in ("encrypted", "signed-encrypted"):
                    body = self._decrypt(body)
                if self.protection in ("signed", "signed-encrypted"):
                    body = self._verify(body)
                message = decode_message(body, self.encoding)
            if message.data["sender"] != self.peer or message.data["recipient"] != self.local:
                raise ProtocolError("opened envelope does not match expected endpoints")
            if message.expired():
                raise ProtocolError("opened envelope has expired")
            return message
        except ProtocolError:
            raise
        except Exception as exc:
            raise ProtocolError("wire record could not be opened under the required policy") from exc
