# Shared mounts: check your backend before getting attached

The directory adapter needs a trusted filesystem with atomic create-if-absent
hard links, coherent reads, and working file flush/fsync calls. Two agents must
actually see the same underlying message/receipt files. Matching path strings on
separate machines do not establish a shared surface.

## What publication does

The writer creates a `.pending-*` file inside `messages/`, writes and flushes the
complete envelope, fsyncs that file, and hard-links it to `messages/<id>.json`
without replacing an existing target. It then removes its own temporary name.
Readers scan only `*.json`; incomplete pending files are not delivered.

An identical retry converges on the existing envelope. A different envelope under
the same ID raises an error and preserves the original. A crash before linking
can leave a pending file with no published message. A crash after linking can
leave both names for the same complete envelope. A temporary-file cleanup error
can therefore be reported **after publication already succeeded**. Re-read the
message ID and reconcile the observed state before retrying; retain the exact ID
and envelope. Do not turn an ambiguous outcome into a fresh logical request.

Publication does not fsync parent directory entries, and receipt marker writes
are not fsynced. Successful API calls and restart tests do not establish power-loss
durability. A remote filesystem may add caching, visibility delays, availability
failures, or semantics different from a local POSIX filesystem. Cloud-sync folders
and independent Git working trees do not acquire shared-filesystem semantics
because they contain the same filenames.

## Two consumers, one task: mind the double dip

Independent consumers can both read the same pending message before either writes
its receipt. Acknowledgment is idempotent and recipient-local; it is not an
exclusive task claim. Broadcast recipients have separate receipt markers.
Applications need an explicit claim/coordination mechanism or idempotent effects
if more than one worker uses the same identity. Persist results before marking a
message handled, and reconcile work after a crash between completion and receipt.

New processes can observe stored messages and receipt markers after normal
process exit. That is restart persistence under the tested filesystem conditions,
not a guarantee against machine failure, storage loss, or delayed remote caches.
Filename scan order is not creation order. Filesystem calls have no adapter-level
deadline and can block when a mount is unavailable.

## Probe the selected mount — no surprise deep cleaning

After installation, select an **existing directory** on the mount:

```bash
ass2ass-mount-probe --directory /explicit/shared/mount --timeout 10
# Without installing the package, from the repository root:
PYTHONPATH=skills/ass2ass/scripts python3 -m ass2ass.mount_probe \
  --directory /explicit/shared/mount --timeout 10
```

No mount is selected, discovered, created or contacted automatically. The command
creates a unique private `.ass2ass-probe-*` scratch directory under the supplied
path and tests only its own files. It checks that file fsync returns, hard links
identify the same file, an existing target is not replaced, identical publications
converge, ID conflicts are rejected, and receipts are visible through fresh handles
and after the worker process exits. It removes only that probe-owned directory.
It never scans, acknowledges, removes or rewrites existing shared message history.

Output is one JSON report. `status: observed` with `scratch_removed: true` exits
zero; failures/timeouts exit nonzero and identify the failed check or error.
`checks` lists completed observations. A cleanup failure reports the retained
scratch path. A worker that cannot be reaped leaves its scratch for inspection.

The worker has a finite userspace deadline and is killed/reaped on normal timeout.
Initial path access, scratch creation, parent verification and cleanup are filesystem
operations too; kernel I/O stuck on an unavailable mount may outlive that deadline.
The timeout is not a guarantee of forcibly interruptible filesystem I/O.

This is a **single-host capability observation**, not certification. It does not
simulate power loss or prove multi-node cache coherence, stable visibility across
all clients, or behavior of every NFS/SMB/FUSE/cloud-sync implementation. For a real
shared mount, repeat the probe on the actual clients and validate cross-client
publication/receipt visibility under your mount options before relying on it.

## Cleanup belongs to the owner

The adapter has no automatic history collection. Expired and acknowledged envelopes
remain available for inspection. Malformed stored records stop reads with an error
and retain their bytes; a receiver never silently repairs or discards them.

An operator owns cleanup of the shared surface. Before archiving/removing history,
quiesce writers and consumers, preserve a snapshot, and decide the intended
retention and recipient-handling policy. A pending file's age alone does not prove
its writer is dead. Do not delete another process's temporary file while it may
still be publishing. Removing receipts while retaining messages can re-enable
delivery; removing messages can destroy evidence needed to reconcile ambiguous
work. Preserve the envelope/receipt relationship in any archive.

The mount probe's scratch cleanup is separate from this shared-history policy.
Retained probe scratch paths are explicitly reported for the owner to inspect and
remove once no probe worker is using them.

## Evidence

`tests/test_filesystem.py` exercises concurrent observations/acknowledgments,
receipt visibility after a real subprocess exit, fsync/link failures, crashes on
both sides of publication, cleanup ambiguity, malformed-record preservation,
probe isolation, unsupported hard links, timeout reaping and retained scratch on
cleanup failure. `tests/test_conformance.py` checks the common contract. These are
local tests; document real mount observations separately.

[Protocol](protocol.md) · [Common contract](adapter-contract.md)
