from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ass2ass.protocol import Message, ProtocolError, MAX_MESSAGE_BYTES
from ass2ass.wire import WireCodec, ENCODINGS, MODES, _b64, _unb64, decode_message, encode_message
from ass2ass.wire_cli import generate_keys, load_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
from pipe_round_trip import connected, round_trip

EXTRAS = importlib.util.find_spec("jwcrypto") is not None and importlib.util.find_spec("cbor2") is not None


class PlainWireTests(unittest.TestCase):
    def test_default_is_the_existing_json_envelope(self):
        message = Message.create("a", "b", {"hello": "world"})
        raw = WireCodec("a", "b").encode(message)
        self.assertEqual(raw, message.to_json().encode())
        self.assertEqual(WireCodec("b", "a").decode(raw).to_json(), message.to_json())

    def test_explicit_identity_and_profile_required(self):
        for local, peer in (("*", "b"), ("a", "a"), ("", "b")):
            with self.assertRaises(ProtocolError):
                WireCodec(local, peer)
        with self.assertRaises(ProtocolError):
            WireCodec("a", "b", encoding="pickle")
        with self.assertRaises(ProtocolError):
            WireCodec("b", "a").decode(Message.create("c", "b", {}).to_json().encode())


@unittest.skipUnless(EXTRAS, "install ass2ass[experimental] for real JOSE/CBOR tests")
class ProtectedWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from jwcrypto import jwk
        cls.jwk = jwk
        cls.sign = jwk.JWK.generate(kty="OKP", crv="Ed25519")
        cls.encrypt = jwk.JWK.generate(kty="EC", crv="P-256")

    def pair(self, encoding="json", protection="signed-encrypted"):
        signed = protection in ("signed", "signed-encrypted")
        encrypted = protection in ("encrypted", "signed-encrypted")
        a = WireCodec("a", "b", encoding=encoding, protection=protection,
                      signing_key=self.sign if signed else None,
                      encryption_key=self.jwk.JWK.from_json(self.encrypt.export_public()) if encrypted else None)
        b = WireCodec("b", "a", encoding=encoding, protection=protection,
                      verification_key=self.jwk.JWK.from_json(self.sign.export_public()) if signed else None,
                      decryption_key=self.encrypt if encrypted else None)
        return a, b

    def test_all_profiles_preserve_the_inner_envelope(self):
        message = Message.create("a", "b", {"unicode": "💌\n", "values": [None, True, -3, 1.5, 2**100]})
        for encoding in ENCODINGS:
            for protection in MODES:
                with self.subTest(encoding=encoding, protection=protection):
                    a, b = self.pair(encoding, protection)
                    self.assertEqual(b.decode(a.encode(message)).to_json(), message.to_json())

    def test_ciphertext_is_standard_jwe_and_contains_no_plaintext(self):
        from jwcrypto import jwe, jws
        a, b = self.pair()
        message = Message.create("a", "b", {"secret": "private parcel"})
        first, second = a.encode(message), a.encode(message)
        self.assertNotEqual(first, second)
        self.assertNotIn(b"private parcel", first)
        encrypted = jwe.JWE(algs=["ECDH-ES", "A256GCM"])
        encrypted.deserialize(_unb64(json.loads(first)["body"]).decode(), key=self.encrypt)
        signed = jws.JWS()
        signed.allowed_algs = ["EdDSA"]
        signed.deserialize(encrypted.payload.decode())
        signed.verify(self.sign, alg="EdDSA")
        self.assertEqual(signed.payload, message.to_json().encode())

    def test_tampering_wrong_keys_and_downgrades_fail_closed(self):
        message = Message.create("a", "b", {"task": "hello"})
        for mode in ("signed", "encrypted", "signed-encrypted"):
            a, b = self.pair(protection=mode)
            wire = a.encode(message)
            with self.subTest(mode=mode):
                with self.assertRaises(ProtocolError):
                    b.decode(message.to_json().encode())
                wrapper = json.loads(wire)
                token = _unb64(wrapper["body"]).decode().split(".")
                field = 2 if mode == "signed" else 3
                token[field] = ("A" if token[field][0] != "A" else "B") + token[field][1:]
                wrapper["body"] = _b64(".".join(token).encode())
                with self.assertRaises(ProtocolError):
                    b.decode(json.dumps(wrapper).encode())
                wrapper = json.loads(wire)
                wrapper["encoding"] = "cbor"
                with self.assertRaises(ProtocolError):
                    b.decode(json.dumps(wrapper).encode())
                if mode in ("signed", "signed-encrypted"):
                    b.verification_key = self.jwk.JWK.generate(kty="OKP", crv="Ed25519")
                else:
                    b.decryption_key = self.jwk.JWK.generate(kty="EC", crv="P-256")
                with self.assertRaises(ProtocolError):
                    b.decode(wire)

    def test_authenticated_header_policy_rejects_other_algorithms(self):
        from jwcrypto import jws
        _, b = self.pair(protection="signed")
        message = Message.create("a", "b", {})
        attacker = jws.JWS(message.to_json().encode())
        attacker.add_signature(self.jwk.JWK.generate(kty="oct", size=256), protected={"alg": "HS256"})
        wrapper = {"wire": "ass2ass/0.1", "encoding": "json", "protection": "signed",
                   "body": _b64(attacker.serialize(compact=True).encode())}
        with self.assertRaises(ProtocolError):
            b.decode(json.dumps(wrapper).encode())

    def test_expired_signed_messages_are_rejected_after_opening(self):
        a, b = self.pair(protection="signed")
        data = Message.create("a", "b", {}).data
        now = datetime.now(timezone.utc)
        data.update(created_at=(now-timedelta(hours=2)).isoformat(), expires_at=(now-timedelta(hours=1)).isoformat())
        message = Message(data)
        with patch.object(Message, "expired", return_value=False):
            wire = a.encode(message)
        with self.assertRaisesRegex(ProtocolError, "expired"):
            b.decode(wire)

    def test_encrypted_only_does_not_authenticate_the_sender(self):
        # Anyone holding the recipient public key can claim a sender. This mode
        # provides confidentiality, not a verified identity; signed-encrypted does both.
        a, b = self.pair(protection="encrypted")
        spoof = Message.create("a", "b", {"claim": "not authenticated"})
        self.assertEqual(b.decode(a.encode(spoof)).id, spoof.id)

    def test_missing_keys_and_keys_in_plaintext_mode_are_errors(self):
        with self.assertRaises(ProtocolError):
            WireCodec("a", "b", signing_key=self.sign)
        with self.assertRaises(ProtocolError):
            WireCodec("a", "b", protection="signed").encode(Message.create("a", "b", {}))
        with self.assertRaises(ProtocolError):
            WireCodec("a", "b", protection="signed", signing_key=self.encrypt).encode(Message.create("a", "b", {}))

    def test_cbor_profile_rejects_extensions_duplicates_and_bad_lengths(self):
        import cbor2
        message = Message.create("a", "b", {"x": 1})
        raw = encode_message(message, "cbor")
        self.assertEqual(decode_message(raw, "cbor").id, message.id)
        invalid = [raw + b"\x00", b"\x9f\xff", b"\x5b" + b"\xff"*8,
                   cbor2.dumps(cbor2.CBORTag(35, "(a+)+")), b"\x81" * 66 + b"\xf6"]
        # Duplicate the first entry of the 11-field envelope's definite map.
        pairs = list(message.data.items())
        duplicates = b"\xac" + b"".join(cbor2.dumps(k) + cbor2.dumps(v) for k, v in pairs + [pairs[0]])
        invalid.append(duplicates)
        for value in invalid:
            with self.subTest(raw=value[:10]), self.assertRaises(ProtocolError):
                decode_message(value, "cbor")
        with self.assertRaises(ProtocolError):
            decode_message(b"x" * (MAX_MESSAGE_BYTES + 1), "cbor")

    def test_keys_are_private_never_overwritten_and_cli_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = generate_keys(folder)
            before = {p: Path(p).read_bytes() for p in paths}
            for p in paths:
                if ".private." in p:
                    self.assertEqual(Path(p).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                generate_keys(folder)
            self.assertEqual(before, {p: Path(p).read_bytes() for p in paths})
            message = Message.create("a", "b", {"hello": "world"})
            common = [sys.executable, "-m", "ass2ass.wire_cli"]
            signed = subprocess.run(common + ["wrap", "--as", "a", "--peer", "b", "--protection", "signed",
                                               "--sign-key", str(Path(folder)/"sign.private.jwk")],
                                    input=message.to_json(), text=True, capture_output=True, check=True, timeout=5)
            opened = subprocess.run(common + ["unwrap", "--as", "b", "--peer", "a", "--protection", "signed",
                                               "--verify-key", str(Path(folder)/"sign.public.jwk")],
                                    input=signed.stdout, text=True, capture_output=True, check=True, timeout=5)
            self.assertEqual(json.loads(opened.stdout), message.data)
            error = subprocess.run(common + ["unwrap", "--as", "b", "--peer", "a", "--protection", "signed",
                                              "--verify-key", str(Path(folder)/"sign.public.jwk")],
                                   input=message.to_json(), text=True, capture_output=True, timeout=5)
            self.assertEqual(error.returncode, 1)
            self.assertEqual(error.stdout, "")

    @unittest.skipUnless(os.name == "posix", "POSIX pipes only")
    def test_all_profiles_through_both_two_process_pipe_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            keys = {}
            for name in ("planner", "builder"):
                directory = Path(folder)/name
                generate_keys(directory)
                keys[name] = {p.name: p for p in directory.iterdir()}
            for mode in ("stdio", "fifo"):
                for encoding in ENCODINGS:
                    for protection in MODES:
                        signed = protection in ("signed", "signed-encrypted")
                        encrypted = protection in ("encrypted", "signed-encrypted")
                        local = dict(encoding=encoding, protection=protection)
                        remote = ["--encoding", encoding, "--protection", protection]
                        for role, file, owner in (("signing_key", "sign.private.jwk", "planner"),
                                                   ("verification_key", "sign.public.jwk", "builder"),
                                                   ("encryption_key", "encrypt.public.jwk", "builder"),
                                                   ("decryption_key", "encrypt.private.jwk", "planner")):
                            if (signed and role in ("signing_key", "verification_key")) or (encrypted and role in ("encryption_key", "decryption_key")):
                                local[role] = load_key(keys[owner][file])
                        if signed:
                            remote += ["--sign-key", str(keys['builder']['sign.private.jwk']), "--verify-key", str(keys['planner']['sign.public.jwk'])]
                        if encrypted:
                            remote += ["--encrypt-key", str(keys['planner']['encrypt.public.jwk']), "--decrypt-key", str(keys['builder']['encrypt.private.jwk'])]
                        with self.subTest(mode=mode, encoding=encoding, protection=protection):
                            with connected(mode, codec=WireCodec("planner", "builder", **local), worker_args=remote) as surface:
                                payload = {"parcel": "same package, optional wrapping 💌"}
                                self.assertEqual(round_trip(surface, payload).data["payload"], {"echo": payload})

    def test_wrapped_frame_larger_than_the_original_limit(self):
        # A valid protected record can exceed 1 MiB while its inner envelope
        # remains smaller. Exercise real framing rather than only byte codecs.
        from protected_round_trip import demo_policy
        with tempfile.TemporaryDirectory() as folder:
            codec, flags = demo_policy(folder, "json", "signed-encrypted")
            with connected("stdio", codec=codec, worker_args=flags) as surface:
                payload = {"data": "x" * 600000}
                self.assertEqual(round_trip(surface, payload).data["payload"], {"echo": payload})
                self.assertGreater(surface.bytes_written, MAX_MESSAGE_BYTES)

    def test_signed_encrypted_cbor_through_fake_ssh(self):
        from protected_round_trip import demo_policy
        from ass2ass.ssh import connected_ssh
        with tempfile.TemporaryDirectory(prefix="ass2ass ssh keys ") as folder:
            codec, flags = demo_policy(folder, "cbor", "signed-encrypted")
            remote_keys = {flag[2:]: value for flag, value in zip(flags[::2], flags[1::2]) if flag.endswith("-key")}
            program = Path(folder)/"fake ssh"
            program.write_text(f"#!{sys.executable}\nimport os,shlex,sys\nargs=shlex.split(sys.argv[-1])\nos.execvp(args[0],args)\n")
            program.chmod(0o700)
            with connected_ssh("not-a-real-host", "planner", "builder", codec=codec,
                               remote_keys=remote_keys, ssh_program=str(program)) as surface:
                response = round_trip(surface, {"parcel": "private, signed and tightly wrapped"})
                self.assertEqual(response.data["payload"], {"echo": {"parcel": "private, signed and tightly wrapped"}})


if __name__ == "__main__":
    unittest.main()
