# The common contract: same envelope, better manners

## Name your layers before touching the plumbing

- **Envelope:** the v0.1 message fields and their meaning.
- **Encoding:** a representation of those fields, such as JSON or the optional CBOR profile.
- **Protection:** optional signatures/encryption around that representation; a configured receiver requires its selected mode.
- **Transport adapter:** executable code publishing, receiving and acknowledging messages on a particular surface.
- **Surface mapping:** instructions for agents using existing authorized tools to carry the same messages. A mapping is not an automated integration.
- **Surface:** the exact shared location or connection the parties actually use.

Read [protocol.md](protocol.md) for normative envelope semantics, and
[layers.md](layers.md) for optional wrapping. Identity strings and location names
do not grant access. Resolve both endpoint identities, the peer-visible location,
and actual tool access before exchanging work.

## Structural schema and semantic checks

[envelope-0.1.schema.json](envelope-0.1.schema.json) is a JSON Schema Draft 2020-12
document for parsed envelope structure. It requires all eleven fields, rejects
unknown fields, and checks version, kinds, canonical UUID spelling and identity
shape. Its reference URI identifies this experimental schema; no registry lookup
is required to validate it locally.

Run the protocol parser too. A structural schema cannot inspect duplicate keys
already lost during JSON parsing, enforce raw UTF-8 byte size, compare timestamps,
filter expired messages at receive time, or establish correlation across messages.
This schema deliberately leaves timestamp parsing to `Message`: Python's supported
timezone-aware ISO 8601 spellings are broader than JSON Schema's RFC 3339 format.
Finite numeric values, unique raw keys, a timezone, and expiry strictly after
creation remain required protocol rules. An expired envelope is structurally and
semantically valid, but is no longer eligible for delivery.

[fixtures-0.1.json](fixtures-0.1.json) preserves **raw JSON strings**, including
duplicate-key and invalid-number examples. `valid` means the protocol parser
accepts the envelope; `schema_valid`, where present, describes structural schema
validation; `expired` describes delivery eligibility. Sequence fixtures exercise
identical retries and conflicting contents under one ID. Keep IDs/content unchanged
when retrying a logical publication.

## Minimum adapter behavior

1. **Setup and discovery:** use an explicit location and identities. No automatic
   account, thread, mount or host discovery is implied. If compatibility is unknown,
   exchange the protocol's hello event and agree on version/capabilities before work.
   Optional protection profiles and trusted keys need trusted configuration; an
   unauthenticated hello cannot authorize a protection downgrade.
2. **Publication:** preserve envelope values. Reject a reused ID with different
   contents; identical retries converge within the adapter's documented lifetime.
   Publication proves only the operation that the medium actually completed.
3. **Receive:** validate before delivery; enforce the recipient, expiry, and receipt
   state. Support broadcast only if independent recipient receipts are implemented.
   Repeated receive may return unacknowledged messages. Stop/report malformed records;
   do not silently delete or execute them.
4. **Reply:** preserve conversation ID and correlate the response/error to the
   original request ID. Return the outcome separately from a handled receipt.
5. **Acknowledge:** only the addressed endpoint may acknowledge a received message.
   A receipt means handled. Append-only/stream profiles use a correlated event with
   `{"type":"receipt","status":"handled"}`. Do not acknowledge receipt events.
6. **Failure and lifetime:** publish bounds, timeout/EOF behavior, ordering, history
   lifetime, persistence and retry limits. Reconcile ambiguous work before retries;
   applications own durable idempotency. Do not promise exactly-once effects.
7. **Capabilities:** publish unsupported features explicitly. A transport requiring
   fixed point-to-point peers can reject broadcast and out-of-route publication
   rather than pretending to implement a shared inbox.

## Capability matrix: know what your backend can handle

| Profile | Ordering | History / receipts | Payload bounds | Broadcast | Identity and access |
| --- | --- | --- | --- | --- | --- |
| Directory | Filename scan order; no temporal/causal guarantee | Stored envelopes and per-recipient markers; restart persistence, no power-loss guarantee | 1 MiB UTF-8 JSON / canonical envelope | Yes, independent receipts | Explicit trusted shared path; sender/receipt claims unauthenticated |
| Anonymous pipes | Per-direction byte order | Session-local, no restart replay | 1 MiB default frame; optional wrapper 3 MiB with 1 MiB inner envelope | No | Fixed endpoint pair, pipe access controls |
| FIFOs | Per-direction byte order with one writer | Same session-local rules; path lifecycle belongs to caller | Same pipe bounds | No | Existing private FIFO paths, fixed peers, POSIX only |
| SSH launcher | Pipe order over the selected SSH channel | Pipe session only | Same pipe bounds | No | Explicit host/peers; normal OpenSSH authentication and strict host-key checks |
| Optional wire protection | Preserves underlying transport order | No extra replay store | Inner encoding/canonical JSON 1 MiB, wrapper 3 MiB | Current codec requires explicit peers | Pinned peer keys; optional JWS/JWE; no automatic key distribution |
| Git / GitHub / GitLab / SharePoint / JIRA / QR | Pending experiments | No implemented automated guarantee | To be specified per child issue | Not claimed | Explicit authorized locations required |

Pipes also bound canonical history to 10,000 envelopes and 16 MiB per direction
by default, including receipts. They use finite operation deadlines. Directory
operations depend on filesystem availability and provide no operation timeout.
Shared mount support requires checking the actual filesystem's atomic hard-link
semantics; a local conformance result does not validate every network mount.
Multiple directory consumers can observe the same pending task; there is no
exclusive claim. Pipes require one owner/reader/writer per direction.

Transport permissions, message authentication, and task authority are separate
concerns. SSH authenticates its transport peer; signed message identity requires
the optional pinned-key signature profile. Encryption-only does not authenticate
the claimed sender. Agents must inspect task data within their existing authority.

## Conformance: bring receipts, not bravado

From the repository root:

```bash
python3 -m pip install '.[test]'
PYTHONPATH=skills/ass2ass/scripts python3 -m unittest discover -s tests -p test_conformance.py -v
```

`tests/test_conformance.py` provides `SurfaceContract`, a reusable unittest mixin.
A new profile supplies fresh `self.a` and `self.b` endpoints in `setUp`, using
`planner` and `builder`, plus cleanup. The shared tests verify content preservation,
repeat-before-ack, correlated response, explicit/idempotent acknowledgment, receipt
ownership, expiry filtering, identical retries and collision rejection.

Override `assert_completed_receipts` for a medium with in-band receipt draining;
the pipe profile demonstrates this. Keep the actual publish/receive/ack operations
in the test, rather than replacing them with mocks. The directory profile uses
two independent handles on one temporary path; the FIFO profile opens two real
FIFOs; the anonymous profile uses actual OS pipes. No shared test assumes temporal
ordering, restart persistence, broadcast, authentication or exclusive execution.

Add profile-specific tests for every stronger claim (broadcast independence,
restart persistence, malformed input, framing, backpressure, timeouts, protection,
remote publication and cleanup). Existing filesystem, pipe, wire and SSH test files
provide those focused checks. A fake-client/SSH test proves its local behavior;
record real service or physical-device validation separately. Keep benchmark results
and support/graduation decisions distinct from protocol conformance.
