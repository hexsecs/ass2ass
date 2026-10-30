# Same package. Optional protection.

ASS2ASS has layers. You choose the wrapping before the encounter; both endpoints
must agree on it. Plain JSON remains the default, with no keys or extra packages.

| Layer | Job | Experimental choices |
| --- | --- | --- |
| Envelope | Who is speaking, to whom, about which request? | The unchanged v0.1 fields |
| Encoding | How are those fields represented? | JSON or deterministic CBOR |
| Protection | Who can open it, and whose signature is on it? | None, signed, encrypted, signed then encrypted |
| Transport | How does it get from one backend to another? | Local pipes/FIFOs or an explicitly configured SSH connection |

The [experimental backend catalogue](experimental-backends.md) covers transport
support. The [codec measurements](codec-metrics.md) show the actual wrapping tax.

## Your package, your choice

```bash
# Existing JSON/directory/pipe workflows need no optional dependencies.
python3 -m pip install .

# Install only the extras you intend to use.
python3 -m pip install '.[cbor]'
python3 -m pip install '.[protection]'
# Or both:
python3 -m pip install '.[experimental]'
```

| `--protection` | What it supplies | Keys required at each duplex endpoint |
| --- | --- | --- |
| `none` (default) | Original plaintext semantics | None |
| `signed` | Sender authentication and integrity using a pinned Ed25519 public key | Own signing private key, peer verification public key |
| `encrypted` | Message confidentiality to the holder of the recipient private key | Peer encryption public key, own decryption private key |
| `signed-encrypted` | Signature verification inside recipient encryption | Both pairs above |

Encryption alone leaves sender identity unverified: anyone holding a recipient's
public encryption key can encrypt a message claiming to be a particular sender.
Choose `signed-encrypted` when the exchange needs both confidentiality and
authenticated origin. Public verification keys must be bound to identities by
your own trusted configuration.

Protection is optional to select, and mandatory to satisfy once selected.
A peer configured for signatures rejects plaintext. A peer configured for
signed-and-encrypted messages rejects encryption-only messages. There is no
automatic fallback, key discovery, or unauthenticated negotiation.

## Local chemistry, with or without wrapping

```bash
# Plain JSON, preserving the existing envelope on the wire:
python3 examples/protected_round_trip.py

# Same request/reply and handled receipts, with optional protection:
python3 examples/protected_round_trip.py --protection signed
python3 examples/protected_round_trip.py --encoding cbor --protection signed-encrypted
python3 examples/protected_round_trip.py --mode fifo --protection encrypted
```

The demo explicitly generates temporary test keys when protection is requested,
passes each endpoint its own keys and its peer's public keys, and removes the
temporary files on exit. Production APIs and workers require supplied keys.
The echo worker returns the payload as data; it does not execute its instructions.
Replies and handled receipts use the same configured protection as requests.

## Know whose ASS you're talking to

Generate keys only when you intend to create a new test identity:

```bash
ass2ass-wire keygen --directory planner-keys
ass2ass-wire keygen --directory builder-keys
```

Each directory contains `sign.private.jwk`, `sign.public.jwk`,
`encrypt.private.jwk`, and `encrypt.public.jwk`. Private files are created with
mode 0600; existing files are never overwritten. The command prints paths, never
private key values. These are unencrypted test-key files: storage, backup,
rotation, revocation, and protecting an endpoint's private keys are deployment
responsibilities. Removing a demo file does not guarantee forensic erasure.

Keep private keys at their owning endpoints. Exchange public keys through a
trusted channel you already control, and pin each public key to its expected
peer identity. A key carried inside an incoming message is not used as a source
of trust. One fixed signing/encryption key per peer is supported in this prototype;
rotate keys by updating both endpoints' explicit configuration.

For a one-message signing experiment after installation:

```bash
python3 -c "from ass2ass import Message; print(Message.create('planner','builder',{'task':'hello'},kind='request').to_json())" > request.json

ass2ass-wire wrap --as planner --peer builder --protection signed \
  --sign-key planner-keys/sign.private.jwk < request.json > parcel.ndjson

ass2ass-wire unwrap --as builder --peer planner --protection signed \
  --verify-key planner-keys/sign.public.jwk < parcel.ndjson
```

`wrap` consumes one ordinary envelope; `unwrap` emits it only after the required
policy and inner-envelope validation succeed. For encryption add `--encrypt-key`
at the sender and `--decrypt-key` at the recipient; use `signed-encrypted` to keep
the signature too. For a duplex pipe worker supply its signing/decryption private
keys and its peer's verification/encryption public keys as appropriate.

## Private packages: what stays visible?

Signatures use compact JWS with EdDSA/Ed25519. Encryption uses compact JWE with
ECDH-ES over P-256 and A256GCM. Signed-and-encrypted mode signs the encoded envelope
first and encrypts that JWS. JWcrypto and cryptography supply the cryptographic
operations. Algorithms, content types, and the profile are pinned; compression,
alternate algorithms, unprotected JOSE headers, and embedded trust keys are outside
this profile.

The outer record exposes its profile version, encoding, protection mode, and
length. JWE exposes its algorithm and ephemeral public key. Encryption hides the
inner envelope, including its sender, recipient, correlation fields, and payload;
the transport still exposes its own connection metadata. Signed-only records are
readable. Authorized receiving processes see decrypted messages and must handle
them according to their own authority.

On opening, the receiver validates the configured keys and protection mode,
checks the inner envelope, requires the expected endpoint identities, and rejects
expired messages. Signatures and encryption do not establish freshness: preserve
message IDs and application-owned durable handled state to manage replays and
ambiguous retries. Pipe history remains session-local. An envelope without an
expiry can remain valid indefinitely under the configured keys.

Wrapping currently integrates with pipes/FIFOs and SSH. The directory adapter
continues to store ordinary plaintext envelopes; pointing it at wrapped records
does not add encryption support. The one-record CLI can produce opaque parcels
for other agreed surfaces, whose routing and receipt behavior need a separate mapping.

## Same package, tighter wrapping

CBOR is a representation of the existing JSON-compatible envelope, not a new set
of message fields. The profile uses CBOR2's canonical encoding, definite lengths,
at most 64 nesting levels, and only JSON-compatible values. Bignum tags 2/3 are
supported for large JSON integers; other semantic tags, duplicate keys, trailing
data, nonfinite numbers, and indefinite-length encoding are rejected. Native binary
payload fields require a future envelope design.

Plain JSON keeps its original NDJSON representation. Other modes use a JSON
wrapper with `wire`, `encoding`, `protection`, and a base64url `body`, followed by
LF. That makes binary CBOR and JOSE tokens safe on the existing newline-framed
transport. Inner encodings and canonical JSON envelopes are limited to 1 MiB;
wrapped records are limited to 3 MiB. The
[measurements](codec-metrics.md) include the entire record: a smaller inner CBOR
message can still have a larger plaintext wire representation.

## Long-distance cheek-to-cheek

SSH carries the pipe worker's stdin/stdout between hosts. Prepare the remote
Python environment with ASS2ASS installed, configure normal SSH authentication
and trusted host keys, then select a host explicitly:

```bash
ass2ass-ssh user@example-host --as planner --peer builder \
  --remote-python /path/to/venv/bin/python --timeout 10
```

The launcher uses batch authentication and strict host-key checking. It does not
accept unknown host keys, request a password, or forward the authentication agent.
Arguments are passed as an argv locally and quoted individually for the remote
POSIX shell. The remote Python argument names one executable; shell command
fragments are not expanded. The worker needs clean stdout for protocol records;
diagnostics belong on stderr. Exchanges and cleanup have finite timeouts.

SSH's encrypted connection is separate from optional message protection. Add
`--encoding` and `--protection` plus the appropriate local key options and
`--remote-sign-key`, `--remote-verify-key`, `--remote-encrypt-key`, and
`--remote-decrypt-key` paths when required. The remote flags name files on the
remote endpoint; keys are not uploaded by the launcher.

| Endpoint | Signing | Verification | Encryption | Decryption |
| --- | --- | --- | --- | --- |
| Planner / local flags | Planner private | Builder public | Builder public | Planner private |
| Builder / remote flags | Builder private | Planner public | Planner public | Builder private |

This is a POSIX echo experiment. Tests exercise the SSH command, local process
plumbing, rejection paths, and cleanup with a fake SSH executable. No real remote
SSH interoperability claim is made from those tests. The same optional-protection
profiles are exercised through real local pipes and FIFOs.

[Codec measurements](codec-metrics.md) · [Experimental backends](experimental-backends.md) · [Roadmap](https://github.com/hexsecs/ass2ass/issues/1)
