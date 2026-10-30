# Optional wrapping: keep the envelope, choose the protection

The `ass2ass.wire.WireCodec` experiment separates encoding and protection from
the unchanged v0.1 envelope. Existing directory/pipe workflows remain plaintext
JSON and need no optional dependencies. CBOR requires `ass2ass[cbor]`; protection
requires `ass2ass[protection]`; `ass2ass[experimental]` includes both.

## Per-peer policy

Construct `WireCodec(local, peer, encoding="json", protection="none", ...)` with
explicit identities. Modes are `none`, `signed`, `encrypted`, and
`signed-encrypted`. Supply JWK objects via `signing_key` (own private),
`verification_key` (trusted peer public), `encryption_key` (peer public), and
`decryption_key` (own private), as required by the selected mode. A duplex endpoint
needs both directions' keys; standalone `encode(message)` or `decode(bytes)` needs
only that operation's keys. Pass the codec to `PipeSurface(..., codec=codec)` or
`PipeSurface.open_fifos(..., codec=codec)`.

There is no format sniffing, unauthenticated negotiation, key discovery, or
protection downgrade. The receiver requires exactly its configured profile,
expected identities and unexpired inner envelope. Expiry is optional in v0.1;
store handled IDs in application-owned durable state for replay/idempotency
across sessions. Receipt events get the same protection as other messages.

## Wire profile

Default JSON/none emits the original compact envelope. Other profiles emit one
JSON wrapper plus LF with exactly `wire: "ass2ass/0.1"`, `encoding`, `protection`,
and `body` (unpadded base64url). The inner encoded envelope is bounded to 1 MiB,
as is its validated canonical JSON representation; the wrapper is bounded to
3 MiB. Pipe framing and session history limits still apply.

CBOR uses CBOR2 canonical encoding with definite lengths and at most 64 levels
of nesting. Values remain JSON-compatible. Only bignum tags 2/3 are allowed;
other semantic tags, duplicate map keys, trailing data, and nonfinite numbers
are rejected before delivery. The profile validator bounds lengths and tags
before library decoding; canonical re-encoding must match the received bytes.

Signatures are compact JWS, EdDSA with Ed25519 keys. Encryption is compact JWE,
ECDH-ES with P-256 keys and A256GCM. Combined mode signs the encoded envelope,
then encrypts the JWS. The JOSE protected header binds the encoding, mode,
content type, and profile type. Algorithms and header fields are restricted;
compression and alternate algorithms are not supported. Incoming keys or key URLs
are never used to establish trust.

Encryption-only permits anyone holding the recipient public key to create a
message claiming a sender. It supplies confidentiality, not verified origin.
Signed-encrypted mode requires the pinned sender signature after decryption.
Outer profile/length, JOSE metadata, and transport metadata remain visible.
Endpoints see plaintext; protection does not secure a compromised endpoint.

## CLI and keys

`ass2ass-wire keygen --directory PATH` explicitly generates local test JWK files:
`sign.private.jwk`, `sign.public.jwk`, `encrypt.private.jwk`, and
`encrypt.public.jwk`. Existing files are not replaced, private files use mode
0600, and output contains paths only. Private files are not password-encrypted;
owners manage storage, backup, rotation and revocation. Share only public keys
through an already trusted channel and bind them to expected peer identities.

`ass2ass-wire wrap --as LOCAL --peer PEER` consumes one ordinary envelope.
`ass2ass-wire unwrap --as LOCAL --peer PEER` consumes one wire record. Both use
`--encoding`, `--protection`, and explicit `--sign-key`, `--verify-key`,
`--encrypt-key`, `--decrypt-key` file paths. A JWK file is limited to 16 KiB.
Invalid input exits nonzero with a JSON error on stderr and no plaintext output.
No message body is automatically executed.

The pipe worker `ass2ass-pipe` accepts the same profile/key options. JSON/none
continues to work without keys. The directory adapter does not automatically
store/decode protected records.

## SSH

`ass2ass-ssh HOST --as LOCAL --peer PEER --remote-python PYTHON` explicitly opens
an SSH pipe to the installed remote echo worker. Host authentication uses normal
OpenSSH configuration with batch mode, strict host-key checking, forwarding
disabled, and finite connection/exchange/cleanup timeouts. Arguments are quoted
for the remote POSIX shell. No automatic host discovery, login, key acceptance,
key upload, or real-device access occurs.

Local profile/key flags configure this endpoint; corresponding
`--remote-sign-key`, `--remote-verify-key`, `--remote-encrypt-key`, and
`--remote-decrypt-key` flags refer to files already provisioned on the remote host.
Both endpoints must agree on the profile. SSH protects the connection;
message-level end-to-end protection remains an independent opt-in choice.
This prototype is POSIX-only. A successful local fake-SSH test is not a tested
remote SSH deployment.
