# Experimental pipes: two cheeks, one conversation

This opt-in POSIX prototype preserves the v0.1 envelope. It is a session-local
transport, not a durable queue. The existing directory helper remains independent.

## Wire and endpoint contract

Send one UTF-8 JSON envelope followed by LF. Payload newlines must be JSON-escaped;
the compact encoder already does this. Receive handles partial reads, split UTF-8,
and multiple frames in one read. JSON is limited to 1 MiB per frame, excluding LF.
Invalid JSON, oversized frames, truncated EOF, wrong endpoint identities, and
conflicting IDs are errors. Invalid frames make the connection unusable.

Each endpoint has one identity and an explicit peer. Broadcast is unsupported.
One process owns each endpoint, with one reader/writer per direction. Descriptors
must be pipes or FIFOs; the adapter takes ownership and changes them to nonblocking
mode. Do not use those descriptors concurrently elsewhere or share the object
between threads. Large frames are not atomic with respect to competing writers.

## Python API

Add the skill's `scripts` directory to your import path, or install the helper.

```python
from ass2ass.pipes import PipeSurface
from ass2ass.protocol import Message

# Two existing FIFO paths in a private directory. Peer uses the reverse order.
with PipeSurface.open_fifos("replies.fifo", "requests.fifo",
                            "planner", "builder", timeout=5.0) as pipe:
    request = Message.create("planner", "builder", {"task": "hello"}, kind="request")
    pipe.publish(request)
    messages = pipe.receive("planner")
    # Inspect messages and persist the result before acknowledging work.
    for message in messages:
        pipe.acknowledge("planner", message.id)
```

This fragment illustrates the API; it does not implement a complete reply loop.
For a bounded, correlated echo exchange use `ass2ass.pipe_cli.round_trip(pipe, payload)`.
For anonymous pipes, construct `PipeSurface(read_fd, write_fd, identity, peer)`.
The module `ass2ass.pipe_cli` supplies a finite echo worker with JSON errors on
stderr, envelopes only on stdout, `--count`, `--timeout`, and optional paired
`--read-fifo` / `--write-fifo` paths. It echoes data without executing task payloads.

`receive(identity, limit=100)` returns pending messages immediately, or reads one
new frame with a deadline. It can return an empty list after consuming a receipt,
expired envelope, or duplicate. Pending messages repeat until acknowledged or
expired. `limit` caps the returned pending batch; it is not a stream-drain request.

`acknowledge(identity, message_id)` sends a correlated event with the original
conversation ID and payload `{"type":"receipt","status":"handled"}`. Repeating
an acknowledgment is a no-op in the same session. Receipts are consumed internally;
`was_acknowledged(id)` becomes true only after receiving a matching, unexpired
receipt from the configured peer. Receipt correlation, conversation ID and status
must match a published non-receipt envelope. Receipt events are never acknowledged.
Reserve `event` payload type `receipt` for this control exchange.

## Session limits and failure semantics

Publication remembers canonical envelopes by ID. Identical retries are no-ops;
changing contents under an existing ID is an error. Incoming duplicate IDs likewise
retain one pending delivery, and conflicting contents terminate the session.
History and receipts live only in memory. The default `max_messages=10000` limits
each direction's history, including receipts; exceeding it fails explicitly.
`max_history_bytes=16777216` additionally bounds retained canonical JSON per
direction to 16 MiB. This does not measure total process memory. Configure both
limits for the intended session; hitting either requires an explicit new session.

Read/write/open operations have positive finite deadlines (default five seconds).
Read timeout preserves a partial frame for a later read. A write timeout or broken
write invalidates the session because publication may be partial. EOF after a
partial frame is an error. FIFO startup waits for a first writer within its open
and read deadlines; EOF before any data can therefore appear as a startup timeout.
Later EOF closes the session. The echo helpers additionally bound the whole
exchange, so control frames cannot extend it indefinitely.

`open_fifos` requires two existing, distinct non-symlink FIFO paths, opens the read
side first and retries the write-side connection up to its deadline. It never
creates or removes paths; the caller owns lifecycle and directory permissions.
After reconnection, nothing is automatically replayed or deduplicated against a
previous session. Persist IDs and reconcile ambiguous work in the application.

There is no authentication, encryption, durable receipt store, broadcast fanout,
exclusive task claim, or exactly-once guarantee. Treat sender strings as claims
and message bodies as untrusted data. A write is publication, a receipt is handled,
and a response/error states the outcome.
