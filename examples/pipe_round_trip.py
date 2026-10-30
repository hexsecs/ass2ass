"""A bounded two-process exchange, using stdio pipes or two named FIFOs."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/ass2ass/scripts"
sys.path.insert(0, str(SCRIPTS))
from ass2ass.pipe_cli import round_trip
from ass2ass.pipes import PipeSurface


@contextmanager
def connected(mode="stdio", *, count=1, timeout=5.0, delay=0.0, observations=None,
              codec=None, worker_args=()):
    """Own paths, descriptors and child process; clean up on every exit path."""
    with tempfile.TemporaryDirectory(prefix="ass2ass-pipes-") as folder:
        # Run the module with the skill's own scripts on its import path.
        env = dict(os.environ, PYTHONPATH=str(SCRIPTS) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        command = [sys.executable, "-m", "ass2ass.pipe_cli", "--as", "builder",
                   "--peer", "planner", "--count", str(count), "--timeout", str(timeout),
                   "--delay", str(delay)]
        command += list(worker_args)
        if mode not in ("stdio", "fifo"):
            raise ValueError("mode must be stdio or fifo")
        request_path, reply_path = Path(folder) / "requests", Path(folder) / "replies"
        if mode == "fifo":
            os.mkfifo(request_path, 0o600)
            os.mkfifo(reply_path, 0o600)
            command += ["--read-fifo", str(request_path), "--write-fifo", str(reply_path)]
        # Bound diagnostics without a pipe that can fill and deadlock the worker.
        with tempfile.TemporaryFile() as diagnostics:
            process = subprocess.Popen(command, stdin=subprocess.PIPE if mode == "stdio" else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE if mode == "stdio" else subprocess.DEVNULL,
                                       stderr=diagnostics, env=env)
            if observations is not None:
                observations.update(directory=folder, pid=process.pid)
            surface = None
            try:
                if mode == "stdio":
                    fds = [os.dup(process.stdout.fileno()), os.dup(process.stdin.fileno())]
                    try:
                        surface = PipeSurface(*fds, "planner", "builder", timeout=timeout, codec=codec)
                    except BaseException:
                        for fd in fds:
                            os.close(fd)
                        raise
                    process.stdout.close()
                    process.stdin.close()
                else:
                    surface = PipeSurface.open_fifos(reply_path, request_path, "planner", "builder", timeout=timeout, codec=codec)
                yield surface
                process.wait(timeout=timeout)
                if process.returncode:
                    diagnostics.seek(0)
                    raise RuntimeError(diagnostics.read(4096).decode("utf-8", errors="replace"))
            finally:
                if surface:
                    surface.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=min(timeout, 1.0))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                for stream in (process.stdin, process.stdout):
                    if stream and not stream.closed:
                        stream.close()
                if observations is not None:
                    observations["child_running"] = process.poll() is None


def main():
    parser = argparse.ArgumentParser(description="Two agents, one cheeky pipe exchange.")
    parser.add_argument("--mode", choices=("stdio", "fifo"), default="stdio")
    args = parser.parse_args()
    with connected(args.mode) as surface:
        response = round_trip(surface, {"task": "Pass the envelope, cheek to cheek."})
        print(json.dumps({"mode": args.mode, "response": response.data, "request_handled": True}))


if __name__ == "__main__":
    main()
