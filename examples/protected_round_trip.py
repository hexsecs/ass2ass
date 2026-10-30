"""Optional wrapping on local pipes; demo keys exist only in a temporary directory."""
import argparse
import json
from pathlib import Path
import tempfile

from pipe_round_trip import connected, round_trip
from ass2ass.wire import WireCodec, ENCODINGS, MODES
from ass2ass.wire_cli import generate_keys, load_key


def demo_policy(folder, encoding, protection):
    """Explicit demo-only provisioning. Production keys must be supplied by owners."""
    signed = protection in ("signed", "signed-encrypted")
    encrypted = protection in ("encrypted", "signed-encrypted")
    args = ["--encoding", encoding, "--protection", protection]
    keys = {}
    if protection != "none":
        for owner in ("planner", "builder"):
            directory = Path(folder)/owner
            generate_keys(directory)
            keys[owner] = directory
    config = {"encoding": encoding, "protection": protection}
    if signed:
        config.update(signing_key=load_key(keys['planner']/"sign.private.jwk"),
                      verification_key=load_key(keys['builder']/"sign.public.jwk"))
        args += ["--sign-key", str(keys['builder']/"sign.private.jwk"),
                 "--verify-key", str(keys['planner']/"sign.public.jwk")]
    if encrypted:
        config.update(encryption_key=load_key(keys['builder']/"encrypt.public.jwk"),
                      decryption_key=load_key(keys['planner']/"encrypt.private.jwk"))
        args += ["--encrypt-key", str(keys['planner']/"encrypt.public.jwk"),
                 "--decrypt-key", str(keys['builder']/"encrypt.private.jwk")]
    return WireCodec("planner", "builder", **config), args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("stdio", "fifo"), default="stdio")
    parser.add_argument("--encoding", choices=ENCODINGS, default="json")
    parser.add_argument("--protection", choices=MODES, default="none")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ass2ass-demo-keys-") as folder:
        policy, flags = demo_policy(folder, args.encoding, args.protection)
        with connected(args.mode, codec=policy, worker_args=flags) as surface:
            response = round_trip(surface, {"task": "Same package. Optional protection."})
            print(json.dumps({"encoding": args.encoding, "protection": args.protection,
                              "response": response.data, "request_handled": True}))


if __name__ == "__main__":
    main()
