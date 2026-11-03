"""Opt-in, scratch-only observations of a selected filesystem. Stdlib only."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

from .filesystem import DirectorySurface
from .protocol import Message, ProtocolError


def observe(root):
    checks = []
    step = 'file_fsync_returned'
    try:
        source, target = root/'link-source', root/'link-target'
        with source.open('xb') as stream:
            stream.write(b'original')
            stream.flush()
            os.fsync(stream.fileno())
        checks.append(step)
        step = 'hardlink_creation'
        os.link(source, target)
        if not os.path.samestat(source.stat(), target.stat()):
            raise OSError('hard-link aliases do not identify the same file')
        checks.append(step)
        step = 'hardlink_no_replace'
        other = root/'other-source'
        other.write_bytes(b'different')
        try:
            os.link(other, target)
        except FileExistsError:
            pass
        else:
            raise OSError('hard-link creation replaced an existing target')
        if target.read_bytes() != b'original':
            raise OSError('existing target content changed')
        checks.append(step)
        step = 'publish_and_identical_retry'
        surface = DirectorySurface(root/'surface')
        message = Message.create('probe.sender', 'probe.receiver', {'type': 'mount-probe'})
        surface.publish(message)
        surface.publish(message)
        received = DirectorySurface(root/'surface').receive('probe.receiver')
        if [m.to_json() for m in received] != [message.to_json()]:
            raise OSError('published envelope was not preserved')
        checks.append(step)
        step = 'conflicting_id_rejected'
        try:
            surface.publish(Message({**message.data, 'payload': {'different': True}}))
        except ProtocolError:
            pass
        else:
            raise OSError('conflicting envelope was accepted')
        if surface.receive('probe.receiver')[0].to_json() != message.to_json():
            raise OSError('conflict changed the original envelope')
        checks.append(step)
        step = 'receipt_visible_to_fresh_handle'
        surface.acknowledge('probe.receiver', message.id)
        if DirectorySurface(root/'surface').receive('probe.receiver'):
            raise OSError('handled message remained visible')
        checks.append(step)
        return {'status': 'observed', 'checks': checks, 'message_id': message.id}
    except (OSError, ValueError, AssertionError) as exc:
        return {'status': 'failed', 'checks': checks, 'failed_check': step,
                'error': str(exc), 'error_type': type(exc).__name__, 'errno': getattr(exc, 'errno', None)}


def probe(directory, timeout=10.0):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be positive and finite')
    parent = Path(directory).resolve(strict=True)
    if not parent.is_dir():
        raise ValueError('select an existing directory on the mount to test')
    root = Path(tempfile.mkdtemp(prefix='.ass2ass-probe-', dir=parent))
    process = None
    report = {'directory': str(parent), 'scratch_directory': str(root),
              'status': 'failed', 'checks': [], 'scratch_removed': False,
              'limits': 'Single-host observations only; no multi-node, power-loss, or universal mount certification.'}
    try:
        command = [sys.executable, '-m', 'ass2ass.mount_probe', '--worker', str(root)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=os.name == 'posix')
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            report.update(status='timeout', error='probe worker exceeded its deadline')
        else:
            if process.returncode:
                report.update(error='probe worker failed', detail=stderr[-4096:])
            else:
                report.update(json.loads(stdout))
                if report['status'] == 'observed':
                    # A separate process has exited. Verify its persisted result.
                    surface = DirectorySurface(root/'surface')
                    if surface.receive('probe.receiver') or not (surface.messages/f"{report['message_id']}.json").is_file():
                        raise OSError('persisted message/receipt failed the parent-process check')
                    report['checks'].append('receipt_visible_after_worker_exit')
    except (OSError, ValueError) as exc:
        report.update(status='failed', error=str(exc))
    finally:
        if process is not None and process.poll() is None:
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                report['error'] = 'worker could not be reaped; scratch retained for operator inspection'
        if process is None or process.poll() is not None:
            try:
                shutil.rmtree(root)
                report['scratch_removed'] = True
            except OSError as exc:
                report.update(status='failed', cleanup_error=str(exc))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check your mount before trusting your backend. Uses private scratch only.')
    parser.add_argument('--directory', type=Path, help='existing directory on the filesystem to probe')
    parser.add_argument('--timeout', type=float, default=10.0, help='worker deadline in seconds; kernel filesystem calls can still block')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker is not None:
        if not args.worker.is_dir() or not args.worker.name.startswith('.ass2ass-probe-'):
            parser.error('worker requires a probe-owned scratch directory')
        print(json.dumps(observe(args.worker)))
        return 0
    if args.directory is None:
        parser.error('--directory is required; no mount is selected automatically')
    try:
        report = probe(args.directory, args.timeout)
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}))
        return 1
    print(json.dumps(report))
    return 0 if report['status'] == 'observed' and report['scratch_removed'] else 1


if __name__ == '__main__':
    sys.exit(main())
