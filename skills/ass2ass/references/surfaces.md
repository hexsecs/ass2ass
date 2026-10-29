# Carry the same envelope on other surfaces

Use only a location and recipient authorized by the task. Do not install integrations, contact unrelated recipients, or create new communication channels just to make a surface available. Confirm that the peer can access the location and knows the same framing.

These are agent-executed mappings, not implemented automated adapters. A common envelope does not make every surface provide the same delivery guarantees.

An opt-in automated POSIX pipe prototype is documented separately in
[pipes.md](pipes.md); it uses session-local receipts and has no durable queue.

## Appendable text: issue comments, chat, documents

Append one complete JSON envelope in a fenced block labeled `ass2ass`:

````text
```ass2ass
{"version":"0.1", ... complete envelope ...}
```
````

Use actual valid JSON; the ellipsis above illustrates framing only. Preserve surrounding human content. Avoid editing existing comments or rewriting an entire shared document. If the tool only supports whole-document replacement without conflict control, stop and choose a safer location with the user.

On receive, inspect the agreed thread or region for complete fenced envelopes, validate them against protocol.md, and process only your recipient ID or broadcasts. Ignore unrelated text. If a candidate ASS2ASS block is malformed, report its location and do not execute it.

Keep a durable per-agent record of handled IDs in user-authorized local state if available. To expose a receipt to the peer, append an `event` envelope addressed to the original sender, correlated to the handled ID, with payload `{"type":"receipt","status":"handled"}`. Do not send receipts for receipt events; record those locally to avoid loops. A receipt confirms handling only; publish a response/error separately for task outcomes.

Before retrying an ambiguous append, search the agreed location for the message ID. Identical existing content counts as published; conflicting content is an error. When the medium lacks atomic conditional writes, concurrent retries can still create duplicate records. Deduplicate at the receiver. Never promise exactly-once delivery.

## Repository files

Use one immutable file per message and a separate per-recipient receipt path using the directory mapping. Use the host's create-if-absent or version-conflict controls. Re-read on a conflict; accept identical content and reject different content at the same ID. Preserve unrelated files. A local working-tree write is not remote publication: verify the message reached the peer-visible ref.

Do not use the local directory adapter on an independently cloned repository and assume it synchronizes agents. Normal version-control synchronization is a separate transport operation.

## Database, queue, object store

When an authorized tool is available, map the ID to a unique key, store the entire JSON envelope, and retain recipient-local receipt state. Prefer conditional inserts and explicit conflict errors. Queues with destructive reads or one shared consumer group cannot provide independent broadcast receipts without additional state; document the difference before using them.

## Capability boundaries

If a surface is inaccessible, read-only, lossy, unable to preserve a full envelope, or unable to retain recipient state, explain the limitation. Offer a complete envelope for manual delivery or propose another shared location. Do not report a manual draft as sent. Do not infer authentication from a sender string or mark a task completed based only on a receipt.
