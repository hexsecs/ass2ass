# Protocol draft 0.1

ASS2ASS means **Agentic Signaling System to Any Shared Surface**. A surface carries immutable JSON envelopes; agents supply the interpretation and execution policy.

## Envelope

All fields below are required, including nullable fields. Reject unknown fields in 0.1. Future incompatible changes require a different version.

```json
{
  "version": "0.1",
  "id": "0515abb4-33b1-4446-a86d-bef52f729440",
  "sender": "planner",
  "recipient": "builder",
  "kind": "request",
  "conversation_id": "114d3648-5e56-470a-a793-e9ed1ba8685a",
  "correlation_id": null,
  "created_at": "2026-10-29T18:00:00+00:00",
  "expires_at": null,
  "payload": {"task": "Review the interface", "done_when": "Return findings"}
}
```

| Field | Rule |
|---|---|
| version | Exactly `0.1` |
| id | Canonical lowercase hyphenated UUID; unique per logical message |
| sender | Nonblank string, at most 256 characters; cannot be `*` |
| recipient | Nonblank string, at most 256 characters; `*` means broadcast |
| kind | `request`, `response`, `event`, `cancel`, or `error` |
| conversation_id | Canonical UUID shared by a conversation |
| correlation_id | UUID of the related message, or null |
| created_at | Timezone-aware ISO 8601 timestamp |
| expires_at | Timezone-aware ISO 8601 timestamp later than created_at, or null |
| payload | JSON object, with application-defined contents |

Use finite JSON numbers and unique string object keys. Limit an envelope to 1 MiB of UTF-8 JSON. The helper emits compact, ASCII-escaped JSON; byte limits apply to the encoding actually transmitted. Identity strings are case-sensitive and surface-scoped. Do not interpolate IDs into paths without validation or encoding.

## Semantics

- `request`: ask a recipient to perform work within its existing authority.
- `response`: return a result; correlate it to the request.
- `event`: announce status, capabilities, or another fact; no response required unless agreed.
- `cancel`: ask the recipient to stop work correlated to a prior request.
- `error`: report a failed request with an actionable explanation.

Use a payload `type` field when agreeing on application-specific events. Arbitrary payloads are carried without automatic execution. Receipt is distinct from completion, and a response is distinct from successful publication.

## Adapter contract

Provide `publish(message) -> id`, `receive(recipient, limit=100) -> messages`, and `acknowledge(recipient, message_id)`. Preserve all envelope values. Publication must not overwrite an existing ID with different contents. A retry of the same ID and envelope is a no-op. Filter receive by exact recipient or broadcast, expiry, and recipient-local acknowledgment. Stop on malformed records and expose errors.

No adapter may claim stronger guarantees than its medium supports. Directory receive returns messages in filename order, which is deliberately not temporal or causal order. There is no delivery deadline, scheduler, exclusive claim, or global ordering. Messages remain pending until acknowledged or expired; readers may receive duplicates. Do not garbage-collect shared messages automatically.

## Directory mapping

Store complete envelopes at `messages/<id>.json`. Write a temporary file on the same filesystem, flush and fsync it, then atomically hard-link it to the final name without replacing anything. Remove the temporary name afterward. Identical retries converge; conflicting IDs fail.

Store receipt markers at `receipts/<sha256(recipient UTF-8)>/<id>`. Use only canonical UUIDs for ID path components. Acknowledgment is local to each recipient, including broadcasts. The directory adapter verifies the original recipient before writing a marker. The marker is empty and idempotent.

The draft assumes trusted surface writers and filesystem semantics. Receipts and identities are not authenticated; malicious writers can forge, edit, or delete data. Power-loss durability of directory entries and receipts is not guaranteed. Crash recovery between work completion and receipt persistence can repeat work. Applications need their own durable idempotency strategy.
