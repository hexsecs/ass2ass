"""An explicit OpenSSH connection carrying the pipe protocol on stdin/stdout."""
from contextlib import contextmanager
import math
import os
import re
import shlex
import subprocess
import tempfile

from .pipes import PipeClosed, PipeSurface
from .protocol import ProtocolError, identity


def ssh_command(host, local, peer, *, remote_python="python3", port=22, timeout=5.0,
                count=1, codec=None, remote_keys=None, ssh_program="ssh"):
    if os.name != "posix":
        raise OSError("experimental SSH pipes require POSIX")
    for name in (local, peer):
        identity(name)
    if local == peer or "*" in (local, peer):
        raise ProtocolError("SSH requires distinct, explicit endpoint identities")
    if (not isinstance(host, str) or host.startswith("-")
            or not re.fullmatch(r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_.:\[\]-]+", host)):
        raise ValueError("use an explicit SSH host/config alias, optionally user@host")
    if (type(port) is not int or not 1 <= port <= 65535 or type(count) is not int or not 1 <= count <= 1000
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("invalid SSH port, count, or timeout")
    if not isinstance(remote_python, str) or not remote_python or "\x00" in remote_python:
        raise ValueError("remote_python must name an executable, not a shell command")
    if codec is not None and (codec.local != local or codec.peer != peer):
        raise ProtocolError("codec identities must match SSH endpoints")
    if codec is not None:
        codec.validate_duplex()
    if remote_keys and (codec is None or codec.protection == "none"):
        raise ProtocolError("remote keys require an explicitly protected codec")
    remote = [remote_python, "-m", "ass2ass.pipe_cli", "--as", peer, "--peer", local,
              "--count", str(count), "--timeout", str(timeout)]
    if codec is not None:
        remote += ["--encoding", codec.encoding, "--protection", codec.protection]
    allowed = {"sign-key", "verify-key", "encrypt-key", "decrypt-key"}
    for flag, path in (remote_keys or {}).items():
        if flag not in allowed or not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("remote keys must be explicit JWK file paths")
        remote += ["--" + flag, path]
    # OpenSSH passes the remote command to a shell. Quote EACH argument for that
    # POSIX shell as well as using argv locally; a local argv alone is insufficient.
    return [ssh_program, "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no",
            "-o", "ConnectionAttempts=1", "-o", f"ConnectTimeout={math.ceil(timeout)}",
            "-p", str(port), "--", host, shlex.join(remote)]


@contextmanager
def connected_ssh(host, local, peer, **kwargs):
    command = ssh_command(host, local, peer, **kwargs)
    timeout = kwargs.get("timeout", 5.0)
    process = None
    surface = None
    with tempfile.TemporaryFile() as diagnostics:
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=diagnostics, start_new_session=True)
            fds = [os.dup(process.stdout.fileno()), os.dup(process.stdin.fileno())]
            try:
                surface = PipeSurface(*fds, local, peer, timeout=timeout, codec=kwargs.get("codec"))
            except BaseException:
                for fd in fds:
                    os.close(fd)
                raise
            process.stdout.close()
            process.stdin.close()
            try:
                yield surface
            except (PipeClosed, TimeoutError, BrokenPipeError) as exc:
                try:
                    process.wait(timeout=min(timeout, .2))
                except subprocess.TimeoutExpired:
                    pass
                if process.poll() not in (None, 0):
                    diagnostics.seek(0)
                    detail = diagnostics.read(4096).decode("utf-8", errors="replace").strip()
                    raise ProtocolError(f"SSH worker failed ({process.returncode}): {detail}") from exc
                raise
            process.wait(timeout=timeout)
            if process.returncode:
                diagnostics.seek(0)
                raise ProtocolError("SSH worker failed: " + diagnostics.read(4096).decode("utf-8", errors="replace"))
        finally:
            if surface:
                surface.close()
            if process:
                if process.poll() is None:
                    # Include any local ProxyCommand process in bounded cleanup.
                    import signal
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=min(timeout, 1.0))
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=1)
                for stream in (process.stdin, process.stdout):
                    if stream and not stream.closed:
                        stream.close()
