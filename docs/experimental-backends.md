# Experimental backend protocols

## Unfinished business, properly enveloped

Welcome to the back room. These backends carry the same ASS2ASS envelope through
different openings. Experimental means opt-in, limited guarantees, and evidence
before bragging rights. The [roadmap](https://github.com/hexsecs/ass2ass/issues/1)
tracks what comes next; the [leaderboard](leaderboard.md) measures what actually runs.

## Curious about your backend? Take it for a spin

Experiment when you want to learn how your agents cooperate, which surfaces they
can actually share, and what happens when the conversation gets messy. A few
small encounters can answer useful questions:

- **A little cross-runtime chemistry.** Have one agent propose an interface and a
  second review it. Exchange only the request, relevant artifact references, and
  findings. Does the common envelope give the reviewer enough context?
- **Same package, different entrance.** Run the local echo example through stdio
  and then FIFOs. Inspect how connection setup and cleanup change while the
  envelope and receipt rules stay the same.
- **Turn up the load.** Run the benchmark with a larger payload or a longer reader
  delay. Does your favourite pipe stay responsive, or earn a place in Least Clogged?
- **See who ghosts whom.** Disconnect a peer or let an operation time out. Can the
  caller distinguish an unanswered request from a handled one? What state would
  your real application need to reconcile before retrying?
- **Bring your own opening.** Sketch a GitHub, JIRA, or SharePoint mapping around
  an existing authorized shared location. Identify where IDs, replies, and receipts
  would live before building an adapter. Those integrations are still roadmap work.

The included worker is an echo partner, not an autonomous reviewer: it tests the
plumbing. Real agent tasks need your own handling logic and existing authority.
Start with a bounded exchange, inspect the evidence, and graduate your backend
when it has earned more than a suggestive name.

| Backend | Status | Entry point | What you get |
| --- | --- | --- | --- |
| Shared filesystem | Existing reference adapter | `ass2ass --surface PATH` | Stored envelopes, recipient-local receipt markers; trusted filesystem required |
| Anonymous pipes / stdin–stdout | Experimental prototype | `python3 examples/pipe_round_trip.py` | Two local processes, bidirectional NDJSON, session-local receipts |
| Named pipes / FIFOs | Experimental prototype | `python3 examples/pipe_round_trip.py --mode fifo` | Two private FIFO paths, one for each direction; POSIX only |
| Git repository files | Roadmap | Not implemented | Versioned envelopes; synchronization still needs a design |
| GitHub | Roadmap | Not implemented | Issues, pull request threads, or discussions |
| GitLab | Roadmap | Not implemented | Issues or merge request discussions |
| JIRA tickets | Roadmap | Not implemented | Ticket descriptions and comments |
| SharePoint | Roadmap | Not implemented | Lists or document libraries |
| QR codes | Roadmap | Not implemented | Visual envelopes, or references to them |

The remote surfaces have [agent-executed mappings](../skills/ass2ass/references/surfaces.md),
not automated integrations. A place on this list does not connect an account or
grant a peer access.

## Pipe dreams you can actually run

Python 3.10+ on POSIX. No third-party runtime packages, service, or installation
required. Run these from the repository root:

```bash
# Two processes, with stdout doing the talking and stdin doing the listening.
python3 examples/pipe_round_trip.py

# Same exchange, two named FIFOs. The example owns and cleans up both paths.
python3 examples/pipe_round_trip.py --mode fifo
```

Each command starts a planner and a builder echo worker, completes one correlated
request/response exchange, sends handled receipts in both directions, and exits.
The worker echoes data; it never executes instructions in a payload. The printed
result includes the response and `request_handled: true` only after the request's
receipt arrives. The response communicates the result; the receipt means handled.

Want to plumb your own? The worker is available as
`PYTHONPATH=skills/ass2ass/scripts python3 -m ass2ass.pipe_cli --help`, or
`ass2ass-pipe --help` after `python3 -m pip install .`. Its stdout is exclusively
the wire; diagnostics go to stderr. For named FIFOs, provide both `--read-fifo`
and `--write-fifo`. Create private FIFO paths yourself and swap their roles at
the other endpoint. The two-process example shows the complete connection setup.

## The small print on the pipe

| Capability | Experimental pipe behavior |
| --- | --- |
| Framing | One complete UTF-8 JSON envelope plus LF; compact encoder escapes payload newlines |
| Size | At most 1 MiB JSON per frame, plus one LF byte |
| Topology | One reader and one writer per direction, two fixed endpoint identities; no broadcast |
| Ordering | Wire order per direction; no global or causal ordering guarantee |
| Receive | Pending envelopes repeat until explicitly acknowledged; expired envelopes are filtered |
| Receipts | Standard correlated `event` with `{"type":"receipt","status":"handled"}`; no receipt loops |
| Retries | Identical publication IDs are no-ops within one live session; conflicting contents fail |
| Bounds | Finite operation deadlines and configurable history limits: 10,000 envelopes and 16 MiB of canonical JSON per direction by default |
| Durability | None. Process exit loses history, pending messages, and receipts |
| Failures | Malformed frames, identity mismatches, partial EOF, collisions and broken writes produce explicit errors |
| Reconnect | Explicit new session; no automatic replay. Caller must reconcile ambiguous prior work |
| Platforms | POSIX pipe descriptors and FIFOs; this prototype does not support Windows named pipes |
| Trust | Local process/path access controls; sender strings and receipts are not authenticated |

The history limits count receipt envelopes too. The byte budget covers retained
canonical JSON, not total process memory. Set both for your workload. Share neither endpoint across threads
or multiple writers: large frames can exceed `PIPE_BUF` and interleave. Let the
owning process create and remove FIFO paths in a private directory.

A successful write proves bytes entered a pipe. It does not prove receipt or
completion. A timeout during a write can leave a partial frame, so that session
becomes unusable. Start a new connection and reconcile IDs in application-owned
durable state before retrying side effects. FIFO startup can wait for a first
writer up to the deadline; anonymous-pipe EOF terminates immediately.

The [bundled pipe reference](../skills/ass2ass/references/pipes.md) documents the
Python API and control-message rules. This is a prototype, not a replacement for
the filesystem adapter's stored delivery semantics.

## Put your backend where your mouth is

```bash
PYTHONPATH=skills/ass2ass/scripts python3 -m unittest discover -s tests -v
python3 benchmarks/pipes.py --output docs/results/pipes.json --markdown docs/leaderboard.md
```

The [leaderboard](leaderboard.md) has the numbers, raw results, category definitions,
and measurement limits. **Fastest ASS in the West**, **Biggest Load**,
**Tightest Pipe**, **Least Clogged**, **Cleanest Wipe**, **No Message Left Behind**,
**Most Lubed**, and **Most Promiscuous Backend** each keep their own honours.
An unmeasured category stays unranked. Nobody gets crowned on cheek alone.

Follow the [pipe experiment](https://github.com/hexsecs/ass2ass/issues/2) for scope
and acceptance criteria. Graduation requires conformance evidence, documented
limits, a complete example, and an explicit support decision.

[Leaderboard](leaderboard.md) · [README](../README.md)
