"""Long-distance cheek-to-cheek: one explicit host, one finite echo exchange."""
import argparse
import json
import subprocess
import sys

from .pipe_cli import round_trip
from .ssh import connected_ssh
from .wire_cli import add_wire_arguments, codec_from_args


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="explicit SSH destination/config alias")
    parser.add_argument("--as", dest="local", required=True)
    parser.add_argument("--peer", required=True)
    parser.add_argument("--remote-python", default="python3")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--payload", default='{"task":"Long-distance cheek-to-cheek."}')
    add_wire_arguments(parser)
    for flag in ("sign-key", "verify-key", "encrypt-key", "decrypt-key"):
        parser.add_argument("--remote-" + flag, help="key path on the remote host; never a key value")
    args = parser.parse_args(argv)
    try:
        codec = codec_from_args(args, args.local, args.peer)
        remote_keys = {flag: getattr(args, "remote_" + flag.replace("-", "_"))
                       for flag in ("sign-key", "verify-key", "encrypt-key", "decrypt-key")
                       if getattr(args, "remote_" + flag.replace("-", "_")) is not None}
        with connected_ssh(args.host, args.local, args.peer, remote_python=args.remote_python,
                           port=args.port, timeout=args.timeout, codec=codec, remote_keys=remote_keys) as surface:
            response = round_trip(surface, json.loads(args.payload))
            print(json.dumps({"response": response.data, "request_handled": True}))
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
