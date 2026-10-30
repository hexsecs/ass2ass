import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest

from ass2ass.pipe_cli import round_trip
from ass2ass.protocol import ProtocolError
from ass2ass.ssh import connected_ssh, ssh_command


@unittest.skipUnless(os.name == "posix", "POSIX SSH pipe prototype")
class SSHTests(unittest.TestCase):
    def test_command_keeps_host_checks_and_quotes_remote_arguments(self):
        python = "/tmp/python with spaces; echo nope"
        command = ssh_command("user@example.test", "planner", "builder", remote_python=python)
        self.assertIn("BatchMode=yes", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("ForwardAgent=no", command)
        self.assertEqual(command[-2], "user@example.test")
        parsed = shlex.split(command[-1])
        self.assertEqual(parsed[0], python)
        self.assertEqual(parsed[parsed.index("--as")+1], "builder")
        for host in ("-oProxyCommand=oops", "host;echo", "host name", ""):
            with self.assertRaises(ValueError):
                ssh_command(host, "a", "b")

    def executable(self, directory, body):
        path = Path(directory)/"fake-ssh"
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o700)
        return str(path)

    def test_echo_through_fake_ssh_without_contacting_a_host(self):
        with tempfile.TemporaryDirectory() as folder:
            program = self.executable(folder, "import os, shlex, sys\nargs=shlex.split(sys.argv[-1])\nos.execvp(args[0], args)\n")
            with connected_ssh("not-a-real-host", "planner", "builder", ssh_program=program) as surface:
                self.assertEqual(round_trip(surface, {"hello": "ssh plumbing"}).data["payload"], {"echo": {"hello": "ssh plumbing"}})

    def test_remote_failure_is_reported_and_stalled_worker_is_reaped(self):
        with tempfile.TemporaryDirectory() as folder:
            program = self.executable(folder, "import sys\nprint('simulated host-key failure',file=sys.stderr)\nsys.exit(255)\n")
            with self.assertRaisesRegex(ProtocolError, "simulated host-key failure"):
                with connected_ssh("test", "a", "b", ssh_program=program, timeout=.2) as surface:
                    surface.receive("a")
            pid_file = Path(folder)/"pid"
            program = self.executable(folder, f"import os,time\nopen({str(pid_file)!r},'w').write(str(os.getpid()))\ntime.sleep(30)\n")
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                with connected_ssh("test", "a", "b", ssh_program=program, timeout=.5) as surface:
                    surface.receive("a")
            self.assertLess(time.monotonic()-started, 3)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)


if __name__ == "__main__":
    unittest.main()
