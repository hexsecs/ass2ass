"""Measure actual inner/wire byte counts for identical fixture envelopes."""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"skills/ass2ass/scripts"))
from ass2ass.protocol import Message
from ass2ass.wire import WireCodec, ENCODINGS, MODES, encode_message, _jose


def fixture(payload):
    return Message(dict(version="0.1", id="00000000-0000-4000-8000-000000000001",
                        conversation_id="00000000-0000-4000-8000-000000000002", correlation_id=None,
                        sender="planner", recipient="builder", kind="request",
                        created_at="2026-01-01T00:00:00+00:00", expires_at=None, payload=payload))


def measure(provenance):
    jwk, _, _ = _jose()
    signing = jwk.JWK.generate(kty="OKP", crv="Ed25519")
    encryption = jwk.JWK.generate(kty="EC", crv="P-256")
    fixtures = {"small request": {"task": "Review the interface", "done_when": "Return findings"},
                "ASCII text": {"text": "x"*4096},
                "Unicode text": {"text": "💌"*512},
                "telemetry": {"samples": list(range(180))}}
    rows = []
    for name, payload in fixtures.items():
        message = fixture(payload)
        for encoding in ENCODINGS:
            inner = encode_message(message, encoding)
            for mode in MODES:
                signed, encrypted = mode in ("signed", "signed-encrypted"), mode in ("encrypted", "signed-encrypted")
                sender = WireCodec("planner", "builder", encoding=encoding, protection=mode,
                                   signing_key=signing if signed else None, encryption_key=encryption if encrypted else None)
                recipient = WireCodec("builder", "planner", encoding=encoding, protection=mode,
                                      verification_key=jwk.JWK.from_json(signing.export_public()) if signed else None,
                                      decryption_key=encryption if encrypted else None)
                wire = sender.encode(message)
                assert recipient.decode(wire).to_json() == message.to_json()
                rows.append(dict(fixture=name, encoding=encoding, protection=mode, inner_bytes=len(inner),
                                 ndjson_bytes=len(wire)+1, round_trip_preserved=True))
    versions = {}
    for name in ("jwcrypto", "cbor2", "cryptography"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "source checkout; see provenance"
    return dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                python=platform.python_version(), dependencies=versions, provenance=provenance,
                source_sha256={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                               ("benchmarks/codecs.py", "skills/ass2ass/scripts/ass2ass/wire.py")},
                fixture_envelopes={name: fixture(payload).data for name, payload in fixtures.items()}, rows=rows)


def render(data, raw_link="results/codecs.json"):
    lines = ["# Same package, tighter wrapping?", "", f"Measured bytes from [raw results]({raw_link}).",
             "", f"Run: {data['created_at']}; Python {data['python']}.", "",
             "Every row wraps the exact same fixture envelope for its scenario. NDJSON includes the final LF. "
             "All 32 encoding/protection combinations were opened and checked against the original envelope. "
             "This is a size comparison, not a latency, throughput, or security-strength ranking.", "",
             "| Fixture | Encoding | Inner bytes | Plain NDJSON | Signed NDJSON | Encrypted NDJSON | Signed + encrypted NDJSON |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for name in data["fixture_envelopes"]:
        for encoding in ENCODINGS:
            rows = [r for r in data["rows"] if r["fixture"] == name and r["encoding"] == encoding]
            values = {r['protection']: r['ndjson_bytes'] for r in rows}
            lines.append(f"| {name} | {encoding} | {rows[0]['inner_bytes']} | " + " | ".join(str(values[m]) for m in MODES) + " |")
    lines += ["", "## Mind the wrapping tax", "",
              "Plain JSON stays the original envelope with no wrapper. Other modes use a JSON record with a "
              "base64url body so the existing newline-framed transport can carry binary CBOR and JOSE tokens. "
              "That transport wrapper costs bytes: a smaller CBOR inner envelope can still produce a larger "
              "plaintext wire record. Measure the whole package, not just the attractive inner layer.", "",
              "Signing and encryption add standard JOSE headers, signatures, ephemeral key material, nonces, "
              "tags and encoding overhead. Encryption uses fresh randomness; sizes here describe these fixtures "
              "and this profile, not every possible message or JOSE configuration.", "",
              "## Reproduce", "", "```bash", "python3 -m pip install '.[experimental]'",
              "python3 benchmarks/codecs.py",
              "python3 benchmarks/codecs.py --render docs/results/codecs.json --markdown docs/codec-metrics.md",
              "```", "", f"Dependency provenance: {data['provenance']}", "",
              "[Optional protection, encodings and SSH](layers.md) · [Pipe leaderboard](leaderboard.md)", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"docs/results/codecs.json")
    parser.add_argument("--markdown", type=Path, default=ROOT/"docs/codec-metrics.md")
    parser.add_argument("--render", type=Path)
    parser.add_argument("--provenance", default="Installed package versions are recorded in the raw results.")
    args = parser.parse_args()
    data = json.loads(args.render.read_text()) if args.render else measure(args.provenance)
    if data.get("schema_version") != 1:
        parser.error("unsupported result schema")
    if not args.render:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(data, indent=2) + "\n")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    raw_link = Path(os.path.relpath(args.render or args.output, args.markdown.parent)).as_posix()
    args.markdown.write_text(render(data, raw_link))
    print(f"Codec metrics: {args.markdown}")


if __name__ == "__main__":
    main()
