# Pipe leaderboard: show us your backend

Generated from [raw results](results/pipes.json). No vibes-based rankings.

Run: 2026-10-29T22:42:56.498608+00:00. Python 3.12.14 on Linux-6.18.55-x86_64-with-glibc2.41.

3 independent sessions per mode; 100 measured exchanges per session after one warmup; 256 payload data bytes; concurrency 1.

RTT includes a response and the request's handled receipt. Throughput uses the same stop-and-wait workload; it is not peak pipelined throughput. Setup and warmup are excluded.

Values below are medians across sessions. The p95 range shows run-to-run variation.

| Backend | p50 RTT ms | p95 RTT ms (range) | p99 RTT ms | Exchanges/s | Payload bytes/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| stdio | 0.461 | 0.771 (0.733–0.927) | 1.140 | 1893.3 | 484697 |
| fifo | 0.478 | 0.935 (0.813–0.948) | 1.426 | 1830.1 | 468513 |

## The cheeky honours

Winners are local observations, not universal claims. Close results need more runs.

| Category | Result | What earned it |
| --- | --- | --- |
| Fastest ASS in the West | stdio | Lowest median p95 RTT in this run |
| Biggest Load | stdio | Most completed stop-and-wait exchanges/s in this run |
| Tightest Pipe | Tie | Both add one LF framing byte per envelope; envelope/receipt costs are separate |
| Least Clogged | stdio | Highest throughput under the fixed slow-reader workload below |
| Cleanest Wipe | See footprint table | Owned test artifacts only; no claim of invisibility |
| No Message Left Behind | See correctness checks | Healthy-run counts and fault conformance are reported separately; no fault-performance ranking |
| Most Lubed | Unranked | Fresh-environment human setup time has not been measured |
| Most Promiscuous Backend | Tie on tested coverage | Both modes exercised on the one OS/runtime reported above; broader portability is unmeasured |

## Least Clogged: bring your own backpressure

20 exchanges/session, 131072 data bytes, 0.002 s worker delay before each request read; same repeat count.

Write wait counts time waiting for writable readiness after EAGAIN. Buffer peak covers only the client's framing buffer, not whole-process memory. OS pipe capacity is platform-dependent.

| Backend | Exchanges/s | Client write wait s | Client peak read buffer bytes |
| --- | ---: | ---: | ---: |
| stdio | 80.7 | 0.0184 | 131403 |
| fifo | 74.3 | 0.0202 | 131403 |

## Cleanest Wipe: count what is actually left

Inspected immediately after child reaping, descriptor closure and temporary-directory cleanup. This measures resources owned by the demo, not audit logs, telemetry or forensic detectability. Cleanup time runs from the last completed exchange to context exit; persistence is not sampled continuously.

| Backend | Remaining owned directories | Live child processes | Extra parent FDs (Linux) | Median cleanup ms |
| --- | ---: | ---: | ---: | ---: |
| stdio | 0 | 0 | 0 | 7.575 |
| fifo | 0 | 0 | 0 | 7.456 |

## No Message Left Behind

Healthy runs: 720 exchanges; 0 missing responses, 0 duplicate response IDs, 0 payload mismatches.

Fault/conformance suite: **passed**. The raw results include its test output. It covers partial/malformed/oversized frames, EOF, broken writes, slow readers, timeouts, collisions and receipt behavior. Passing tests is not a measured recovery-time score.

## Reproduce the results

```bash
python3 benchmarks/pipes.py --messages 100 --repeats 3 --payload-bytes 256 --slow-messages 20 --slow-payload-bytes 131072 --reader-delay 0.002 --timeout 5 --deadline 60
# Re-render the exact table without running new trials:
python3 benchmarks/pipes.py --render docs/results/pipes.json --markdown docs/leaderboard.md
```

The JSON records exact workload settings, all RTT samples, failures, environment and measurement boundaries. The runner has per-exchange and overall deadlines. A failed run exits nonzero and writes a failure record instead of a new leaderboard. No combined score: different cheeks, different strengths.

[Experimental backend protocols](experimental-backends.md) · [README](../README.md)
