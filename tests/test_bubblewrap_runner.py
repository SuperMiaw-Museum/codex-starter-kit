"""Exercise the opt-in isolation through the actual direct supervisor offline."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import shutil
import subprocess
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'bootstrap'
NPM = os.environ.get('CODEX_TEST_REAL_NPM')
BWRAP = shutil.which('bwrap')
FACTORY = '''
from bubblewrap_exec import OfflineBubblewrap
class Fixture(OfflineBubblewrap):
    def codex_argv(self, resume):
        return ['/usr/bin/python3', '-B', '/project/driver.py', *super().codex_argv(resume)]

def factory(project, protected):
    (project / 'host-supervisor-pid').write_text(str(os.getpid()))
    return Fixture(project, LAUNCHER, protected)

sys.exit(main(sys.argv[1:], offline_isolation_factory=factory))
'''
DRIVER = '''
from pathlib import Path
import json, os, subprocess, sys
from test_codex_exec_sandbox import responses_fixture, fixture_config

config = Path(os.environ['CODEX_HOME'])
print(json.dumps(dict(type='item.completed', item=dict(type='agent_message',
                 text='fixture stdout ' + chr(27) + '[31m'))), flush=True)
print('fixture stderr ' + chr(27) + '[31m', file=sys.stderr, flush=True)
with responses_fixture('/usr/bin/python3 -B /project/probe.py') as url:
    (config / 'config.toml').write_text(fixture_config(url, 'danger-full-access'))
    result = subprocess.run(sys.argv[1:], timeout=12)
    sys.exit(result.returncode)
'''


@unittest.skipUnless(NPM and BWRAP, 'requires explicit local npm Codex and Bubblewrap')
class BubblewrapRunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='kit-protected-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bundle = self.root / 'bootstrap'
        shutil.copytree(SOURCE, self.bundle)
        self.project = self.root / 'project'
        self.project.mkdir(mode=0o700)
        state = tempfile.TemporaryDirectory(prefix='kit-protected-state-', dir='/dev/shm')
        self.addCleanup(state.cleanup)
        self.state = Path(state.name)
        self.receipt = self.state / 'receipt.json'
        self.registry = self.root / 'registry'
        registry = self.bundle / 'internal/task_registry.py'
        registry.write_text(registry.read_text().replace(
            "Path('/tmp') / f'codex-starter-kit-tasks-{os.getuid()}'", f'Path({str(self.registry)!r})'
        ).replace("entries = os.listdir('/dev/pts')", 'entries = []'))
        locking = self.bundle / 'internal/project_lock.py'
        self.locks = self.root / 'locks'
        locking.write_text(locking.read_text().replace(
            "Path('/tmp') / f'codex-starter-kit-project-locks-{os.getuid()}'",
            f'Path({str(self.locks)!r})'))
        spec = importlib.util.spec_from_file_location('fixture_locking', locking)
        self.locking = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.locking)
        self.runner = self.bundle / 'internal/run_codex.py'
        self.runner.write_text(self.runner.read_text().replace(
            '    sys.exit(main(sys.argv[1:]))', f'    LAUNCHER = {NPM!r}\n' + FACTORY))
        shutil.copy2(Path(__file__).with_name('test_codex_exec_sandbox.py'), self.project)
        (self.project / 'driver.py').write_text(DRIVER)
        self.control = self.state / 'receipt-canary'
        self.neighbor = self.root / 'other-project'
        self.neighbor.mkdir()
        self.neighbor_file = self.neighbor / 'canary'
        for path in (self.control, self.neighbor_file):
            path.write_text('untouched')
        (self.project / 'outside-link').symlink_to(self.control)
        self.env = dict(os.environ, CODEX_STARTER_KIT_LIVE_INSPECT='0', LOT6_SECRET_CANARY='hidden')

    def command(self, fd):
        return [str(self.bundle / 'orchestration/run-codex.sh'), '--attempt-id', 'fixture',
                '--receipt', str(self.receipt), '--reservation-fd', str(fd), str(self.project),
                '--task', 'Run the local fixture command.']

    def write_probe(self, *, wait=False):
        targets = [str(self.control), str(self.neighbor_file),
                   str(self.locks / 'barrier-canary'), '/proc/1/root' + str(self.control)]
        (self.project / 'probe.py').write_text(f'''
import json, os, pathlib, subprocess, time
targets = {targets!r}
denied = 0
for name in targets:
    target = pathlib.Path(name)
    for operation in (lambda: target.write_text('changed'), lambda: target.unlink(),
                      lambda: target.rename(target.with_name('moved'))):
        try:
            operation()
        except OSError:
            denied += 1
try:
    pathlib.Path('/project/outside-link').write_text('changed')
except OSError:
    denied += 1
descriptors = []
for fd in pathlib.Path('/proc/self/fd').iterdir():
    try:
        descriptors.append(os.readlink(fd))
    except OSError:
        pass
evidence = dict(denied=denied, inherited=descriptors,
                secret=os.environ.get('LOT6_SECRET_CANARY'),
                home=os.environ.get('HOME'),
                host_pid_visible=(pathlib.Path('/proc') / pathlib.Path('host-supervisor-pid').read_text()).exists(),
                nested_userns=subprocess.run(['/usr/bin/unshare', '--user', '/bin/true'],
                                             capture_output=True).returncode == 0)
pathlib.Path('evidence.tmp').write_text(json.dumps(evidence))
pathlib.Path('evidence.tmp').replace('evidence')
if {wait!r}:
    if os.fork():
        os.wait()
        raise SystemExit(0)
    os.setsid()
    pathlib.Path('detached').touch()
    deadline = time.monotonic() + 6
    while not pathlib.Path('release').exists() and time.monotonic() < deadline:
        time.sleep(.02)
    if pathlib.Path('release').exists():
        pathlib.Path('late').touch()
else:
    pathlib.Path('written').touch()
''')

    def test_real_protected_runner_keeps_control_outside_and_writes_receipt(self):
        self.write_probe()
        stdout = self.state / 'host-stdout'
        stderr = self.state / 'host-stderr'
        with self.locking.reserve_project(self.project) as fd, stdout.open('w') as output, stderr.open('w') as errors:
            barrier = self.locks / 'barrier-canary'
            barrier.write_text('untouched')
            result = subprocess.run(self.command(fd), env=self.env, pass_fds=(fd,),
                                    stdout=output, stderr=errors, timeout=18)
            self.assertEqual(result.returncode, 0, stdout.read_text() + stderr.read_text())
            self.assertEqual(stderr.read_text().count('START Codex'), 1)
            self.assertEqual(stdout.read_text().count('END Codex'), 1)
            self.assertIn(r'fixture stdout \x1b[31m', stdout.read_text())
            self.assertIn(r'fixture stderr \x1b[31m', stderr.read_text())
            self.assertEqual(barrier.read_text(), 'untouched')
        evidence = json.loads((self.project / 'evidence').read_bytes())
        self.assertEqual(evidence['denied'], 13)
        self.assertIsNone(evidence['secret'])
        self.assertEqual(evidence['home'], '/home/worker')
        self.assertFalse(evidence['host_pid_visible'])
        self.assertFalse(evidence['nested_userns'])
        self.assertFalse(any(str(self.root) in fd or str(self.state) in fd or fd.endswith(('.lock', '.active'))
                             for fd in evidence['inherited']), evidence)
        self.assertTrue((self.project / 'written').exists())
        self.assertEqual(self.control.read_text(), 'untouched')
        self.assertEqual(self.neighbor_file.read_text(), 'untouched')
        self.assertEqual(json.loads(self.receipt.read_bytes())['result']['kind'], 'codex')

    def test_protected_runner_rejects_hardlinks_and_sockets_before_start(self):
        alias = self.project / 'alias'
        for make_alias, expected in (
            (lambda: os.link(self.neighbor_file, alias), 'hard-linked'),
            (lambda: local_socket.bind(str(alias)), 'special file'),
        ):
            with socket.socket(socket.AF_UNIX) as local_socket:
                make_alias()
                with self.locking.reserve_project(self.project) as fd:
                    result = subprocess.run(self.command(fd), env=self.env, pass_fds=(fd,),
                                            capture_output=True, text=True, timeout=8)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertNotIn('START Codex', result.stderr)
                self.assertEqual(json.loads(self.receipt.read_bytes())['result']['kind'], 'infrastructure_error')
                alias.unlink()
                self.receipt = self.state / 'another-receipt.json'

    def test_bubblewrap_start_failure_never_falls_back_to_host_execution(self):
        self.write_probe()
        source = self.runner.read_text()
        self.runner.write_text(source.replace('    def codex_argv(self, resume):', """
    def command(self, resume):
        argv = super().command(resume)
        argv.insert(1, '--invalid-fixture-option')
        return argv

    def codex_argv(self, resume):"""))
        with self.locking.reserve_project(self.project) as fd:
            result = subprocess.run(self.command(fd), env=self.env, pass_fds=(fd,),
                                    capture_output=True, text=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid-fixture-option', result.stderr)
        self.assertFalse((self.project / 'evidence').exists())
        self.assertNotEqual(json.loads(self.receipt.read_bytes())['result']['exit_code'], 0)

    def test_detached_process_sigterm(self):
        self.interrupt_detached(signal.SIGTERM)

    def test_detached_process_sigkill(self):
        self.interrupt_detached(signal.SIGKILL)

    def interrupt_detached(self, interruption):
        self.write_probe(wait=True)
        diagnostic = self.root / 'diagnostic'
        with self.locking.reserve_project(self.project) as fd, diagnostic.open('w') as stderr:
            with subprocess.Popen(self.command(fd), env=self.env, pass_fds=(fd,),
                                  stdout=subprocess.DEVNULL, stderr=stderr) as runner:
                try:
                    deadline = time.monotonic() + 10
                    while not (self.project / 'detached').exists():
                        self.assertIsNone(runner.poll(), diagnostic.read_text(errors='replace'))
                        self.assertLess(time.monotonic(), deadline, diagnostic.read_text(errors='replace'))
                        time.sleep(.02)
                    runner.send_signal(interruption)
                    code = runner.wait(timeout=8)
                    if interruption == signal.SIGTERM:
                        self.assertIn(code, (143, 76), diagnostic.read_text())
                        record = json.loads(self.receipt.read_bytes())['result']
                        self.assertEqual(record['kind'], 'cancelled' if code == 143 else 'unknown')
                    else:
                        self.assertEqual(code, -signal.SIGKILL)
                        self.assertFalse(self.receipt.exists())
                finally:
                    if runner.poll() is None:
                        runner.kill()
                        runner.wait(timeout=5)
        if code == 143:
            with self.locking.reserve_project(self.project):
                self.assertFalse((self.project / 'late').exists())
        else:
            with self.assertRaises(self.locking.ProjectUnknown):
                with self.locking.reserve_project(self.project):
                    self.fail('Uncertain interruption retained no fence')
        (self.project / 'release').touch()
        time.sleep(.25)
        self.assertFalse((self.project / 'late').exists())
