# ASS2ASS

![ASS2ASS: green terminal-style robots exchanging an envelope. A cheeky protocol. A proper envelope.](docs/assets/ass2ass-banner.png)

**Agentic Signaling System to Any Shared Surface**

An agent skill for structured communication and coordination over any shared medium: files, repositories, issue threads, documents, chat channels, and more.

Give two agents the skill, a common surface, and stable identities. They can exchange requests, results, events, and cancellation requests using the same small JSON envelope, regardless of runtime or model. No central service is required.

## Start with the skill

The installable skill lives in [`skills/ass2ass`](skills/ass2ass/SKILL.md). Copy that complete directory into your agent runtime's skills directory, following its installation mechanism. Load `SKILL.md` if your runtime supports instructions but has no skill installer. The folder includes protocol references, surface mappings, and an optional Python helper; it is self-contained.

Example invocation:

> Use ASS2ASS to exchange messages with the builder agent through `/shared/project`. My agent ID is planner; the peer ID is builder. Ask it to review the proposed interface and return findings.

Both agents must actually have access to the agreed surface. Installing the skill does not connect accounts, share local files across machines, start a daemon, or grant permissions.

## Try it locally

Python 3.10+; no third-party runtime dependencies. Run from this repository:

```bash
python3 skills/ass2ass/scripts/ass2ass_cli.py --surface .shared send \
  --from planner --to builder --kind request \
  --payload '{"task":"Review the proposed interface","done_when":"Return findings"}'

python3 skills/ass2ass/scripts/ass2ass_cli.py --surface .shared receive --as builder
```

After handling the request, send a `response` to `planner` using the received `conversation_id` and request ID as `--correlation-id`, then acknowledge the request:

```bash
python3 skills/ass2ass/scripts/ass2ass_cli.py --surface .shared ack \
  --as builder MESSAGE_UUID
```

Receive prints one JSON envelope per line and does not acknowledge automatically. The same message can be read again until acknowledged. Use `python3 examples/round_trip.py` for a complete isolated request/reply demonstration.

Optionally install the helper with `python3 -m pip install .` to get the `ass2ass` command and Python imports. The skill itself does not require package installation.

## Included in this first version

- Agent instructions for channel setup, capability exchange, handoffs, deduplication, replies, acknowledgments, and bounded checks.
- Draft 0.1 envelopes with addressing, message IDs, conversation correlation, expiration, and JSON payloads.
- Tested shared-directory helper with atomic publication, collision detection, recipient filtering, independent broadcast acknowledgments, and a JSON CLI.
- Agent-executed mappings for issue threads, chat, documents, repository files, and database/queue surfaces. These mappings require available authorized tools; automated adapters for them are not implemented yet.

## Delivery and trust

This is an experimental protocol draft. Messages may be delivered repeatedly until acknowledged. A receipt means handled; a response or error communicates the task outcome. There is no exactly-once execution, exclusive task claim, global ordering, authentication, encryption, or background listener.

The directory helper requires a trusted filesystem with atomic hard links. Do not assume identical behavior on cloud-sync folders or every network filesystem. Malformed stored messages produce an explicit error instead of being silently discarded. Use surface access controls and application-level idempotency; never execute a message merely because it claims to be from an agent.

Read the [protocol](skills/ass2ass/references/protocol.md) and [surface mappings](skills/ass2ass/references/surfaces.md) for precise rules and limitations.

## Development

```bash
PYTHONPATH=skills/ass2ass/scripts python3 -m unittest discover -s tests -v
```

Next: interoperability fixtures, a JSON Schema, a second automated surface adapter, then authenticated identity and optional work-claim semantics. Preserve the skill-first design: agents should be able to use the envelope through existing tools without adopting a new service.
