"""Reproducible local pipe trials and a Markdown leaderboard. Stdlib only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
from pipe_round_trip import connected, round_trip


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def descriptor_count():
    directory = Path("/proc/self/fd")
    return len(list(directory.iterdir())) if directory.exists() else None


def bounded_run(command, *, timeout, **kwargs):
    """A deadline also reaps the trial's worker, not just its direct parent."""
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True, **kwargs) as process:
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        return subprocess.CompletedProcess(command, process.returncode, out, err)


def trial(mode, messages, payload_bytes, delay, timeout):
    payload = {"data": "x" * payload_bytes}
    observations = {}
    before_fds = descriptor_count()
    samples, missing, malformed, duplicate = [], 0, 0, 0
    ids = set()
    started = time.perf_counter()
    with connected(mode, count=messages + 1, timeout=timeout, delay=delay,
                   observations=observations) as surface:
        warmup = round_trip(surface, payload)
        first_exchange_ms = (time.perf_counter() - started) * 1000
        if warmup.data["payload"] != {"echo": payload}:
            raise ValueError("warmup payload was not preserved")
        bytes_before = surface.bytes_written
        wait_before = surface.write_wait_seconds
        measured_start = time.perf_counter()
        for _ in range(messages):
            one = time.perf_counter()
            response = round_trip(surface, payload)
            samples.append((time.perf_counter() - one) * 1000)
            malformed += response.data["payload"] != {"echo": payload}
            duplicate += response.id in ids
            ids.add(response.id)
        elapsed = time.perf_counter() - measured_start
        missing = messages - len(ids)
        sent_bytes = surface.bytes_written - bytes_before
        blocked = surface.write_wait_seconds - wait_before
        buffer_peak = surface.peak_buffer_bytes
        cleanup_start = time.perf_counter()
    cleanup_ms = (time.perf_counter() - cleanup_start) * 1000
    after_fds = descriptor_count()
    artifacts = {
        "owned_directory_remaining": int(Path(observations["directory"]).exists()),
        "child_running": int(observations["child_running"]),
        "extra_parent_descriptors": max(0, after_fds - before_fds) if before_fds is not None else None,
    }
    return {
        "mode": mode, "messages": messages, "payload_data_bytes": payload_bytes,
        "payload_json_bytes": len(json.dumps(payload, separators=(",", ":")).encode()),
        "concurrency": 1, "reader_delay_seconds": delay,
        "latency_ms": samples, "p50_ms": percentile(samples, .50),
        "p95_ms": percentile(samples, .95), "p99_ms": percentile(samples, .99),
        "elapsed_seconds": elapsed, "completed_exchanges_per_second": messages / elapsed,
        "payload_bytes_per_second": messages * payload_bytes / elapsed,
        "client_wire_bytes": sent_bytes, "client_write_wait_seconds": blocked,
        "client_peak_read_buffer_bytes": buffer_peak,
        "framing_bytes_per_envelope": 1, "first_exchange_ms": first_exchange_ms,
        "cleanup_ms": cleanup_ms, "remaining_artifacts": artifacts,
        "missing": missing, "duplicates": duplicate, "malformed_payloads": malformed,
    }


def median(rows, key):
    return statistics.median(row[key] for row in rows)


def render(data, raw_link="results/pipes.json"):
    if data.get("schema_version") != 1:
        raise ValueError("unsupported benchmark result schema")
    config = data["config"]
    lines = ["# Pipe leaderboard: show us your backend", "",
             f"Generated from [raw results]({raw_link}). No vibes-based rankings.", "",
             f"Run: {data['created_at']}. Python {data['environment']['python']} on {data['environment']['platform']}.", "",
             f"{config['repeats']} independent sessions per mode; {config['messages']} measured exchanges "
             f"per session after one warmup; {config['payload_bytes']} payload data bytes; concurrency 1.", "",
             "RTT includes a response and the request's handled receipt. Throughput uses the same "
             "stop-and-wait workload; it is not peak pipelined throughput. Setup and warmup are excluded.", "",
             "Values below are medians across sessions. The p95 range shows run-to-run variation.", "",
             "| Backend | p50 RTT ms | p95 RTT ms (range) | p99 RTT ms | Exchanges/s | Payload bytes/s |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    groups = {}
    slow_groups = {}
    for mode in data["modes"]:
        rows = [r for r in data["trials"] if r["mode"] == mode]
        groups[mode] = rows
        slow_groups[mode] = [r for r in data["slow_reader_trials"] if r["mode"] == mode]
        p95 = [r["p95_ms"] for r in rows]
        lines.append(f"| {mode} | {median(rows, 'p50_ms'):.3f} | {statistics.median(p95):.3f} "
                     f"({min(p95):.3f}–{max(p95):.3f}) | {median(rows, 'p99_ms'):.3f} | "
                     f"{median(rows, 'completed_exchanges_per_second'):.1f} | {median(rows, 'payload_bytes_per_second'):.0f} |")
    fastest = min(groups, key=lambda m: median(groups[m], "p95_ms"))
    biggest = max(groups, key=lambda m: median(groups[m], "completed_exchanges_per_second"))
    unclogged = max(slow_groups, key=lambda m: median(slow_groups[m], "completed_exchanges_per_second"))
    lines += ["", "## The cheeky honours", "",
              "Winners are local observations, not universal claims. Close results need more runs.", "",
              "| Category | Result | What earned it |", "| --- | --- | --- |",
              f"| Fastest ASS in the West | {fastest} | Lowest median p95 RTT in this run |",
              f"| Biggest Load | {biggest} | Most completed stop-and-wait exchanges/s in this run |",
              "| Tightest Pipe | Tie | Both add one LF framing byte per envelope; envelope/receipt costs are separate |",
              f"| Least Clogged | {unclogged} | Highest throughput under the fixed slow-reader workload below |",
              "| Cleanest Wipe | See footprint table | Owned test artifacts only; no claim of invisibility |",
              "| No Message Left Behind | See correctness checks | Healthy-run counts and fault conformance are reported separately; no fault-performance ranking |",
              "| Most Lubed | Unranked | Fresh-environment human setup time has not been measured |",
              "| Most Promiscuous Backend | Tie on tested coverage | Both modes exercised on the one OS/runtime reported above; broader portability is unmeasured |",
              "", "## Least Clogged: bring your own backpressure", "",
              f"{config['slow_messages']} exchanges/session, {config['slow_payload_bytes']} data bytes, "
              f"{config['reader_delay_seconds']} s worker delay before each request read; same repeat count.", "",
              "Write wait counts time waiting for writable readiness after EAGAIN. Buffer peak covers only the "
              "client's framing buffer, not whole-process memory. OS pipe capacity is platform-dependent.", "",
              "| Backend | Exchanges/s | Client write wait s | Client peak read buffer bytes |",
              "| --- | ---: | ---: | ---: |"]
    for mode, rows in slow_groups.items():
        lines.append(f"| {mode} | {median(rows, 'completed_exchanges_per_second'):.1f} | "
                     f"{median(rows, 'client_write_wait_seconds'):.4f} | {median(rows, 'client_peak_read_buffer_bytes'):.0f} |")
    lines += ["", "## Cleanest Wipe: count what is actually left", "",
              "Inspected immediately after child reaping, descriptor closure and temporary-directory cleanup. "
              "This measures resources owned by the demo, not audit logs, telemetry or forensic detectability. "
              "Cleanup time runs from the last completed exchange to context exit; persistence is not sampled continuously.", "",
              "| Backend | Remaining owned directories | Live child processes | Extra parent FDs (Linux) | Median cleanup ms |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for mode, rows in groups.items():
        artifacts = [r["remaining_artifacts"] for r in rows]
        fds = [a["extra_parent_descriptors"] for a in artifacts]
        fd_text = "not measured" if any(v is None for v in fds) else str(max(fds))
        lines.append(f"| {mode} | {max(a['owned_directory_remaining'] for a in artifacts)} | "
                     f"{max(a['child_running'] for a in artifacts)} | {fd_text} | {median(rows, 'cleanup_ms'):.3f} |")
    counts = [r for r in data["trials"] + data["slow_reader_trials"]]
    command = (f"python3 benchmarks/pipes.py --messages {config['messages']} --repeats {config['repeats']} "
               f"--payload-bytes {config['payload_bytes']} --slow-messages {config['slow_messages']} "
               f"--slow-payload-bytes {config['slow_payload_bytes']} --reader-delay {config['reader_delay_seconds']} "
               f"--timeout {config['timeout_seconds']} --deadline {config['deadline_seconds']}")
    lines += ["", "## No Message Left Behind", "",
              f"Healthy runs: {sum(r['messages'] for r in counts)} exchanges; "
              f"{sum(r['missing'] for r in counts)} missing responses, {sum(r['duplicates'] for r in counts)} "
              f"duplicate response IDs, {sum(r['malformed_payloads'] for r in counts)} payload mismatches.", "",
              f"Fault/conformance suite: **{data['conformance']['status']}**. "
              "The raw results include its test output. It covers partial/malformed/oversized frames, EOF, "
              "broken writes, slow readers, timeouts, collisions and receipt behavior. Passing tests is not a measured recovery-time score.", "",
              "## Reproduce the results", "", "```bash",
              command,
              "# Re-render the exact table without running new trials:",
              "python3 benchmarks/pipes.py --render docs/results/pipes.json --markdown docs/leaderboard.md",
              "```", "", "The JSON records exact workload settings, all RTT samples, failures, environment and "
              "measurement boundaries. The runner has per-exchange and overall deadlines. A failed run exits nonzero "
              "and writes a failure record instead of a new leaderboard. No combined score: different cheeks, different strengths.", "",
              "[Codec size measurements](codec-metrics.md) · [Experimental backend protocols](experimental-backends.md) · [README](../README.md)", ""]
    return "\n".join(lines)


def run(config, scratch):
    started = time.monotonic()
    data = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "machine": platform.machine(), "cpu_count": os.cpu_count()},
            "config": config, "modes": ["stdio", "fifo"], "trials": [], "slow_reader_trials": [],
            "measurement": {"latency": "request publication through response plus handled request receipt",
                            "throughput": "stop-and-wait, concurrency one, excludes setup and warmup",
                            "footprint": "owned resources immediately after cleanup; no system-wide inspection"}}
    source_paths = ["benchmarks/pipes.py", "examples/pipe_round_trip.py",
                    "skills/ass2ass/scripts/ass2ass/pipes.py", "skills/ass2ass/scripts/ass2ass/pipe_cli.py",
                    "skills/ass2ass/scripts/ass2ass/protocol.py", "tests/test_pipes.py"]
    data["source_sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in source_paths}
    try:
        for repeat in range(config["repeats"]):
            # Alternate order so one mode does not always get the cold machine.
            modes = data["modes"] if repeat % 2 == 0 else list(reversed(data["modes"]))
            for mode in modes:
                for slow in (False, True):
                    remaining = config["deadline_seconds"] - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError("overall benchmark deadline exceeded")
                    # One trial runs in its own process so even an implementation
                    # regression cannot defeat the overall deadline.
                    command = [sys.executable, str(Path(__file__).resolve()), "--trial", mode,
                               "--messages", str(config["slow_messages"] if slow else config["messages"]),
                               "--payload-bytes", str(config["slow_payload_bytes"] if slow else config["payload_bytes"]),
                               "--reader-delay", str(config["reader_delay_seconds"] if slow else 0),
                               "--timeout", str(config["timeout_seconds"])]
                    result = bounded_run(command, timeout=remaining,
                                         env=dict(os.environ, TMPDIR=scratch))
                    if result.returncode:
                        raise RuntimeError(result.stderr[-4096:])
                    row = json.loads(result.stdout)
                    row["repeat"] = repeat + 1
                    data["slow_reader_trials" if slow else "trials"].append(row)
        env = dict(os.environ, PYTHONPATH=str(ROOT / "skills/ass2ass/scripts"))
        remaining = config["deadline_seconds"] - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("overall benchmark deadline exceeded")
        suite = bounded_run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_pipes.py", "-v"],
                            cwd=ROOT, env=dict(env, TMPDIR=scratch), timeout=remaining)
        data["conformance"] = {"status": "passed" if suite.returncode == 0 else "failed",
                               "output": suite.stdout + suite.stderr}
        if suite.returncode or any(r["missing"] or r["duplicates"] or r["malformed_payloads"]
                                   for r in data["trials"] + data["slow_reader_trials"]):
            raise RuntimeError("correctness checks failed")
        data["status"] = "passed"
    except Exception as exc:
        data.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/results/pipes.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "docs/leaderboard.md")
    parser.add_argument("--render", type=Path)
    parser.add_argument("--messages", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--payload-bytes", type=int, default=256)
    parser.add_argument("--slow-messages", type=int, default=20)
    parser.add_argument("--slow-payload-bytes", type=int, default=131072)
    parser.add_argument("--reader-delay", type=float, default=.002)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--deadline", type=float, default=60)
    parser.add_argument("--trial", choices=("stdio", "fifo"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (not 1 <= args.messages <= 1000 or not 1 <= args.slow_messages <= 1000
            or not 1 <= args.repeats <= 20 or not 0 <= args.payload_bytes <= 200000
            or not 0 <= args.slow_payload_bytes <= 200000
            or any(not math.isfinite(v) or v <= 0 for v in (args.timeout, args.deadline))
            or not math.isfinite(args.reader_delay) or args.reader_delay < 0):
        parser.error("counts, payloads and timings must be finite and within documented bounds")
    if os.name != "posix":
        parser.error("experimental pipe benchmarks require POSIX")
    if args.trial:
        print(json.dumps(trial(args.trial, args.messages, args.payload_bytes, args.reader_delay, args.timeout)))
        return 0
    if args.render:
        data = json.loads(args.render.read_text())
    else:
        config = {"messages": args.messages, "repeats": args.repeats,
                  "payload_bytes": args.payload_bytes, "slow_messages": args.slow_messages,
                  "slow_payload_bytes": args.slow_payload_bytes, "reader_delay_seconds": args.reader_delay,
                  "timeout_seconds": args.timeout, "deadline_seconds": args.deadline}
        with tempfile.TemporaryDirectory(prefix="ass2ass-bench-") as scratch:
            data = run(config, scratch)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = (args.output if data["status"] == "passed" else
                  args.output.with_name(args.output.stem + ".failed" + args.output.suffix))
        output.write_text(json.dumps(data, indent=2) + "\n")
    if data["status"] != "passed":
        print(data.get("error", "benchmark failed"), file=sys.stderr)
        return 1
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    raw_link = Path(os.path.relpath(args.render or args.output, args.markdown.parent)).as_posix()
    args.markdown.write_text(render(data, raw_link))
    print(f"Leaderboard: {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
