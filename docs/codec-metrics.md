# Same package, tighter wrapping?

Measured bytes from [raw results](results/codecs.json).

Run: 2026-10-30T01:19:33.679727+00:00; Python 3.12.14.

Every row wraps the exact same fixture envelope for its scenario. NDJSON includes the final LF. All 32 encoding/protection combinations were opened and checked against the original envelope. This is a size comparison, not a latency, throughput, or security-strength ranking.

| Fixture | Encoding | Inner bytes | Plain NDJSON | Signed NDJSON | Encrypted NDJSON | Signed + encrypted NDJSON |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| small request | json | 329 | 330 | 997 | 1212 | 1889 |
| small request | cbor | 281 | 446 | 912 | 1127 | 1774 |
| ASCII text | json | 4375 | 4376 | 8191 | 8406 | 11479 |
| ASCII text | cbor | 4333 | 5849 | 8116 | 8331 | 11379 |
| Unicode text | json | 6423 | 6424 | 11831 | 12046 | 16333 |
| Unicode text | cbor | 2285 | 3118 | 4475 | 4690 | 6525 |
| telemetry | json | 891 | 892 | 1996 | 2211 | 3219 |
| telemetry | cbor | 575 | 838 | 1435 | 1650 | 2471 |

## Mind the wrapping tax

Plain JSON stays the original envelope with no wrapper. Other modes use a JSON record with a base64url body so the existing newline-framed transport can carry binary CBOR and JOSE tokens. That transport wrapper costs bytes: a smaller CBOR inner envelope can still produce a larger plaintext wire record. Measure the whole package, not just the attractive inner layer.

Signing and encryption add standard JOSE headers, signatures, ephemeral key material, nonces, tags and encoding overhead. Encryption uses fresh randomness; sizes here describe these fixtures and this profile, not every possible message or JOSE configuration.

## Reproduce

```bash
python3 -m pip install '.[experimental]'
python3 benchmarks/codecs.py
python3 benchmarks/codecs.py --render docs/results/codecs.json --markdown docs/codec-metrics.md
```

Dependency provenance: JWcrypto release v1.5.6 and CBOR2 release 5.6.5 sources fetched from their upstream GitHub repositories; cryptography 50.0.0 installed in the runtime. No dependency source is vendored in this repository.

[Optional protection, encodings and SSH](layers.md) · [Pipe leaderboard](leaderboard.md)
