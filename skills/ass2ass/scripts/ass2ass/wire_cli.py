"""Explicit one-message wrapping/opening and local test-key generation."""
import argparse
import json
import os
from pathlib import Path
import sys

from .protocol import MAX_MESSAGE_BYTES, Message, ProtocolError
from .wire import ENCODINGS, MODES, MAX_WIRE_BYTES, WireCodec, _jose, _json


def add_wire_arguments(parser):
    parser.add_argument("--encoding", choices=ENCODINGS, default="json")
    parser.add_argument("--protection", choices=MODES, default="none")
    for flag in ("sign-key", "verify-key", "encrypt-key", "decrypt-key"):
        parser.add_argument("--" + flag, type=Path, help="explicit JWK file; never discovered automatically")


def load_key(path):
    if path is None:
        return None
    with Path(path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ProtocolError("JWK file exceeds 16 KiB")
    jwk, _, _ = _jose()
    try:
        return jwk.JWK(**_json(raw.decode("utf-8")))
    except Exception as exc:
        raise ProtocolError("invalid explicit JWK file") from exc


def codec_from_args(args, local, peer):
    keys = {"signing_key": load_key(args.sign_key), "verification_key": load_key(args.verify_key),
            "encryption_key": load_key(args.encrypt_key), "decryption_key": load_key(args.decrypt_key)}
    if args.encoding == "json" and args.protection == "none" and all(k is None for k in keys.values()):
        return None
    return WireCodec(local, peer, encoding=args.encoding, protection=args.protection, **keys)


def generate_keys(directory):
    """Generate test identities explicitly, with no overwrite and private mode 0600."""
    jwk, _, _ = _jose()
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    pairs = {"sign": jwk.JWK.generate(kty="OKP", crv="Ed25519"),
             "encrypt": jwk.JWK.generate(kty="EC", crv="P-256")}
    created = []
    try:
        for name, key in pairs.items():
            for private in (True, False):
                path = directory / f"{name}.{'private' if private else 'public'}.jwk"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
                created.append(path)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(key.export(private_key=private) + "\n")
    except BaseException:
        for path in created:
            path.unlink()
        raise
    return [str(p) for p in created]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Same package, optional protection. One envelope per invocation.")
    commands = parser.add_subparsers(dest="command", required=True)
    keys = commands.add_parser("keygen", help="generate local test keys; print paths, never private material")
    keys.add_argument("--directory", required=True, type=Path)
    for name in ("wrap", "unwrap"):
        command = commands.add_parser(name)
        command.add_argument("--as", dest="local", required=True)
        command.add_argument("--peer", required=True)
        add_wire_arguments(command)
    args = parser.parse_args(argv)
    try:
        if args.command == "keygen":
            print(json.dumps({"created": generate_keys(args.directory)}))
            return 0
        codec = codec_from_args(args, args.local, args.peer) or WireCodec(args.local, args.peer)
        cap = MAX_MESSAGE_BYTES if args.command == "wrap" else MAX_WIRE_BYTES
        raw = sys.stdin.buffer.read(cap + 2)
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        if len(raw) > cap:
            raise ProtocolError("input exceeds the record size limit")
        if args.command == "wrap":
            result = codec.encode(Message.from_json(raw.decode("utf-8")))
        else:
            result = codec.decode(raw).to_json().encode("utf-8")
        sys.stdout.buffer.write(result + b"\n")
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
