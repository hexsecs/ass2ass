"""An explicitly opt-in echo worker, never a task executor."""
import argparse
from contextlib import contextmanager
import json
import math
import os
import sys
import time

from .pipes import PipeSurface
from .protocol import Message, ProtocolError


@contextmanager
def budget(surface):
    """Bound the entire exchange, including control frames, by one deadline."""
    timeout = surface.timeout
    deadline = time.monotonic() + timeout

    def remaining():
        surface.timeout = deadline - time.monotonic()
        if surface.timeout <= 0:
            raise TimeoutError("pipe exchange timed out")

    try:
        yield remaining
    finally:
        surface.timeout = timeout


def round_trip(surface, payload):
    """Return only after a correlated response AND a handled receipt arrive."""
    request = Message.create(surface.recipient, surface.peer, payload, kind="request")
    response = None
    with budget(surface) as remaining:
        remaining()
        surface.publish(request)
        while response is None or not surface.was_acknowledged(request.id):
            remaining()
            for message in surface.receive(surface.recipient):
                data = message.data
                if (response is not None or data["kind"] != "response"
                        or data["correlation_id"] != request.id
                        or data["conversation_id"] != request.data["conversation_id"]):
                    raise ProtocolError("unexpected reply in echo exchange")
                response = message
                remaining()
                surface.acknowledge(surface.recipient, message.id)
    return response


def echo(surface, count=1, delay=0.0):
    """Echo data and acknowledge handling; never interpret payload commands."""
    for _ in range(count):
        with budget(surface) as remaining:
            if delay:
                time.sleep(min(delay, surface.timeout))
            request = None
            while request is None:
                remaining()
                received = surface.receive(surface.recipient)
                if received:
                    request = received[0]
            if request.data["kind"] != "request":
                raise ProtocolError("echo worker accepts requests only")
            response = request.reply(surface.recipient, {"echo": request.data["payload"]})
            remaining()
            surface.publish(response)
            remaining()
            surface.acknowledge(surface.recipient, request.id)
            while not surface.was_acknowledged(response.id):
                remaining()
                if surface.receive(surface.recipient):
                    raise ProtocolError("expected response receipt before the next request")


def main(argv=None):
    parser = argparse.ArgumentParser(description="ASS2ASS experimental pipe echo worker: data in, data out.")
    parser.add_argument("--as", dest="recipient", required=True)
    parser.add_argument("--peer", required=True)
    parser.add_argument("--read-fifo")
    parser.add_argument("--write-fifo")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds per complete exchange")
    parser.add_argument("--count", type=int, default=1, help="requests before exit")
    parser.add_argument("--delay", type=float, default=0.0, help="test-only delay before each request read")
    args = parser.parse_args(argv)
    if bool(args.read_fifo) != bool(args.write_fifo):
        parser.error("specify both FIFO paths, or neither for stdin/stdout")
    if args.count < 1 or not math.isfinite(args.delay) or args.delay < 0:
        parser.error("count must be positive and delay finite and nonnegative")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive and finite")
    try:
        if args.read_fifo:
            surface = PipeSurface.open_fifos(args.read_fifo, args.write_fifo,
                                             args.recipient, args.peer, timeout=args.timeout)
        else:
            # stdout is the wire; all errors go to stderr. Duplicate ownership
            # avoids closing Python's actual stdio objects on context exit.
            fds = [os.dup(sys.stdin.fileno()), os.dup(sys.stdout.fileno())]
            try:
                surface = PipeSurface(*fds, args.recipient, args.peer, timeout=args.timeout)
            except BaseException:
                for fd in fds:
                    os.close(fd)
                raise
        with surface:
            echo(surface, args.count, args.delay)
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
