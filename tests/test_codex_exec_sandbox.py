"""Opt-in offline exercise of the real npm Codex exec and its tool sandbox."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest


BOOTSTRAP = Path(__file__).resolve().parents[1] / 'bootstrap'
WRITER = '''
import json, os, pathlib, sys, time

if os.fork():
    os.wait()
    raise SystemExit(0)
os.setsid()
os.closerange(3, 4096)
for fd in (0, 1, 2):
    os.close(fd)
def write_probe(directory):
    try:
        with (pathlib.Path(directory) / "fixture-write-check").open("xb") as probe:
            probe.write(b"isolated fixture probe")
        return True
    except OSError:
        return False

pathlib.Path("ready.tmp").write_text(json.dumps({
    "lock_directory_writable": write_probe(sys.argv[1]),
    "state_directory_writable": write_probe(sys.argv[2]),
    "namespace_pid": os.getpid(),
}))
pathlib.Path("ready.tmp").replace("ready")
deadline = time.monotonic() + 10
while not pathlib.Path("release").exists() and time.monotonic() < deadline:
    time.sleep(.02)
if pathlib.Path("release").exists():
    pathlib.Path("written").touch()
'''


@contextmanager
def responses_fixture(command):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != '/v1/responses':
                self.send_error(404)
                return
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            tool_returned = any(isinstance(item, dict) and item.get('type') == 'function_call_output'
                                for item in request.get('input', ()))
            if tool_returned:
                item = {'type': 'message', 'id': 'msg_1', 'role': 'assistant',
                        'status': 'completed', 'content': [
                            {'type': 'output_text', 'text': 'done', 'annotations': []}]}
            else:
                item = {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1',
                        'name': 'exec_command', 'status': 'completed',
                        'arguments': json.dumps({'cmd': command, 'yield_time_ms': 10000})}
            response = {'id': 'resp_2' if tool_returned else 'resp_1', 'object': 'response',
                        'status': 'completed', 'output': [item], 'usage': {
                            'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2,
                            'input_tokens_details': {'cached_tokens': 0},
                            'output_tokens_details': {'reasoning_tokens': 0}}}
            events = [
                ('response.created', {'response': dict(response, status='in_progress', output=[])}),
                ('response.output_item.done', {'item': item, 'output_index': 0}),
                ('response.completed', {'response': response}),
            ]
            stream = ''.join(f'event: {kind}\ndata: {json.dumps(dict(data, type=kind))}\n\n'
                             for kind, data in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(stream)))
            self.end_headers()
            try:
                self.wfile.write(stream)
            except (BrokenPipeError, ConnectionResetError):
                pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/v1'
        finally:
            server.shutdown()
            thread.join()


def fixture_config(url, mode):
    return f"""model = "gpt-5.4"
model_provider = "fixture"
sandbox_mode = "{mode}"
approval_policy = "never"
[model_providers.fixture]
name = "Fixture"
base_url = "{url}"
wire_api = "responses"
supports_websockets = false
"""


@unittest.skipUnless(os.environ.get('CODEX_TEST_REAL_NPM'), 'requires explicit local npm launcher path')
class RealCodexExecTests(unittest.TestCase):
    def writer_identity(self, script, namespace_pid):
        argument = os.fsencode(script)
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():
                continue
            try:
                if argument not in (process / 'cmdline').read_bytes().split(b'\0'):
                    continue
                for line in (process / 'status').read_bytes().splitlines():
                    if line.startswith(b'NSpid:') and int(line.split()[-1]) == namespace_pid:
                        state, *fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
                        if state != 'Z':
                            return process, fields[18]
            except (OSError, ValueError, IndexError):
                continue
        self.fail('Could not match the fixture writer in host procfs')

    def writer_alive(self, identity):
        process, start = identity
        try:
            state, *fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
            return state != 'Z' and fields[18] == start
        except (OSError, ValueError, IndexError):
            return False

    def wait_for(self, path, runner, diagnostic, timeout=8):
        deadline = time.monotonic() + timeout
        while not path.exists():
            if runner.poll() is not None:
                self.fail(f'Runner exited before {path.name}: {diagnostic.read_text(errors="replace")}')
            self.assertLess(time.monotonic(), deadline, diagnostic.read_text(errors='replace'))
            time.sleep(.02)

    def run_scenario(self, mode, interrupt):
        launcher = Path(os.environ['CODEX_TEST_REAL_NPM']).absolute()
        with tempfile.TemporaryDirectory(prefix='codex-exec-e2e-') as temporary:
            root = Path(temporary)
            bundle = root / 'bootstrap'
            shutil.copytree(BOOTSTRAP, bundle)
            runner_source = bundle / 'tools/run_codex.py'
            runner_source.write_text(runner_source.read_text().replace(
                '            project_lock.require_verified_execution_profile()',
                '            pass  # isolated, intentionally unprotected protocol fixture'))
            project, home, state = (root / name for name in ('project', 'home', 'state'))
            for directory in (project, home, state):
                directory.mkdir(mode=0o700)
            locks = root / 'project-locks'
            lock_source = bundle / 'tools/project_lock.py'
            lock_source.write_text(lock_source.read_text().replace(
                "Path('/tmp') / f'codex-starter-kit-project-locks-{os.getuid()}'",
                f'Path({str(locks)!r})'))
            spec = importlib.util.spec_from_file_location('fixture_lock', lock_source)
            locking = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(locking)
            registry = bundle / 'tools/task_registry.py'
            registry.write_text(registry.read_text().replace(
                "Path('/tmp') / f'codex-starter-kit-tasks-{os.getuid()}'",
                f'Path({str(root / "registry")!r})').replace("entries = os.listdir('/dev/pts')", 'entries = []'))
            controller = project / 'writer.py'
            controller.write_text(WRITER)
            command = shlex.join([sys.executable, '-B', str(controller), str(locks), str(state)])
            with responses_fixture(command) as url:
                config = home / '.codex'
                config.mkdir()
                (config / 'config.toml').write_text(fixture_config(url, mode))
                # No user config, credentials, proxy, or feature overrides.
                env = {'HOME': str(home), 'CODEX_HOME': str(config),
                       'PATH': f'{launcher.parent}:/usr/bin:/bin', 'LANG': 'C.UTF-8'}
                receipt = state / 'receipt.json'
                diagnostic = root / 'stderr'
                output = root / 'stdout'
                with output.open('w') as stdout, diagnostic.open('w') as stderr, subprocess.Popen(
                    [str(bundle / 'tools/run-codex.sh'), '--attempt-id', 'offline',
                     '--receipt', str(receipt), str(project), '--task', 'Run the fixture command.'],
                    cwd=project, env=env, stdout=stdout, stderr=stderr,
                ) as runner:
                    try:
                        self.wait_for(project / 'ready', runner, diagnostic)
                        observed = json.loads((project / 'ready').read_bytes())
                        self.assertEqual(observed['lock_directory_writable'],
                                         (locks / 'fixture-write-check').is_file())
                        self.assertEqual(observed['state_directory_writable'],
                                         (state / 'fixture-write-check').is_file())
                        identity = self.writer_identity(controller, observed['namespace_pid'])
                        with self.assertRaises(locking.ProjectBusy):
                            with locking.reserve_project(project):
                                self.fail('A live writer must exclude competing admission')
                        if interrupt is None:
                            (project / 'release').touch()
                            code = runner.wait(timeout=8)
                            record = json.loads(receipt.read_bytes())['result']
                            self.assertTrue((project / 'written').exists())
                            if code == 76:
                                self.assertEqual(record['kind'], 'unknown')
                                self.assertEqual((record['stage'], record['process_exit']),
                                                 ('quiescence', 0))
                                with self.assertRaises(locking.ProjectUnknown):
                                    with locking.reserve_project(project):
                                        self.fail('Unconfirmed completion must retain the fence')
                            else:
                                self.assertEqual(code, 0, diagnostic.read_text())
                                with locking.reserve_project(project):
                                    self.assertFalse(self.writer_alive(identity))
                                self.assertEqual(record['kind'], 'codex')
                        else:
                            runner.send_signal(interrupt)
                            code = runner.wait(timeout=8)
                            alive = self.writer_alive(identity)
                            if interrupt == signal.SIGKILL:
                                self.assertEqual(code, -signal.SIGKILL)
                                self.assertFalse(receipt.exists())
                                self.assertTrue(alive, 'Crash did not exercise a surviving writer')
                            else:
                                self.assertIn(code, (76, 143), diagnostic.read_text())
                                record = json.loads(receipt.read_bytes())['result']
                                self.assertEqual(record['kind'], 'unknown' if code == 76 else 'cancelled')
                                self.assertEqual(record['signal'], signal.SIGTERM)
                            self.assertFalse((project / 'written').exists())
                            if code == 143:
                                with locking.reserve_project(project):
                                    self.assertFalse(alive, 'Admitted another writer too early')
                            else:
                                with self.assertRaises(locking.ProjectUnknown):
                                    with locking.reserve_project(project):
                                        self.fail('A crash or ambiguous cancellation must retain the fence')
                            if alive:
                                (project / 'release').touch()
                                deadline = time.monotonic() + 4
                                while not (project / 'written').exists() and time.monotonic() < deadline:
                                    time.sleep(.02)
                                self.assertTrue((project / 'written').exists(), diagnostic.read_text())
                        print(f'Codex exec {mode} signal={interrupt} exit={code} '
                              f'lock_dir_writable={observed["lock_directory_writable"]} '
                              f'state_dir_writable={observed["state_directory_writable"]}', flush=True)
                        if code == 76:
                            for line in diagnostic.read_text().splitlines():
                                if line.startswith('Observed children:'):
                                    print(line, flush=True)
                    finally:
                        (project / 'release').touch()
                        if runner.poll() is None:
                            runner.kill()
                            runner.wait(timeout=5)
                if interrupt != signal.SIGKILL:
                    self.assertEqual(diagnostic.read_text().count('START Codex'), 1)
                    self.assertEqual(output.read_text().count('END Codex') +
                                     output.read_text().count('ERROR Codex'), 1)
                return observed

    def test_offline_exec_normal_and_interruptions(self):
        for interrupt in (None, signal.SIGTERM, signal.SIGKILL):
            with self.subTest(interrupt=interrupt):
                self.run_scenario('workspace-write', interrupt)

    def test_unrestricted_profile_can_write_in_coordination_directory(self):
        observed = self.run_scenario('danger-full-access', None)
        self.assertTrue(observed['lock_directory_writable'])
        self.assertTrue(observed['state_directory_writable'])

    @unittest.skipUnless(shutil.which('bwrap'), 'requires bubblewrap for the namespace proof')
    def test_kernel_namespace_denies_mutation_of_control_files_at_same_uid(self):
        with tempfile.TemporaryDirectory(prefix='codex-control-mount-') as temporary:
            root = Path(temporary)
            control = root / 'control'
            project = root / 'project'
            for directory in (control, project):
                directory.mkdir(mode=0o700)
            for name in ('fixture-barrier', 'fixture-receipt'):
                (control / name).write_bytes(b'untouched')
                (control / name).chmod(0o600)
            probe = """
import json, os, pathlib
control = pathlib.Path('/control')
denied = []
for name in ('fixture-barrier', 'fixture-receipt'):
    target = control / name
    operations = (lambda: target.write_bytes(b'changed'),
                  lambda: target.unlink(),
                  lambda: target.rename(control / ('moved-' + name)))
    for operation in operations:
        try:
            operation()
        except OSError as error:
            denied.append(error.errno)
pathlib.Path('/project/written').touch()
pathlib.Path('/project/evidence.json').write_text(json.dumps({'uid': os.getuid(), 'denied': denied}))
"""
            (project / 'probe.py').write_text(probe)
            node = Path(os.environ['CODEX_TEST_REAL_NPM']).absolute().parent.parent
            driver = """
import os, pathlib, subprocess, sys
sys.path.insert(0, '/fixture')
from test_codex_exec_sandbox import responses_fixture, fixture_config
home = pathlib.Path('/project/home')
config = home / '.codex'
config.mkdir(parents=True)
with responses_fixture('/usr/bin/python3 -B /project/probe.py') as url:
    (config / 'config.toml').write_text(fixture_config(url, 'danger-full-access'))
    env = {'HOME': str(home), 'CODEX_HOME': str(config),
           'PATH': '/toolchain/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    result = subprocess.run(['/toolchain/bin/codex', 'exec', '--skip-git-repo-check',
                             '--ephemeral', '--json', '-'], env=env, cwd='/project',
                            input='Run the fixture command.', text=True, timeout=8)
    sys.exit(result.returncode)
"""
            result = subprocess.run([
                shutil.which('bwrap'), '--unshare-all', '--unshare-user', '--disable-userns',
                '--cap-drop', 'ALL', '--new-session', '--die-with-parent', '--clearenv',
                '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
                '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
                '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                '--ro-bind', str(control), '/control', '--bind', str(project), '/project',
                '--ro-bind', str(node), '/toolchain',
                '--ro-bind', str(Path(__file__).resolve()), '/fixture/test_codex_exec_sandbox.py',
                '--', '/usr/bin/python3', '-B', '-c', driver,
            ], capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"type":"turn.completed"', result.stdout)
            evidence = json.loads((project / 'evidence.json').read_bytes())
            self.assertEqual(evidence['uid'], os.getuid())
            self.assertEqual(len(evidence['denied']), 6)
            self.assertEqual(sorted(p.name for p in control.iterdir()),
                             ['fixture-barrier', 'fixture-receipt'])
            self.assertTrue(all(p.read_bytes() == b'untouched' for p in control.iterdir()))
            self.assertTrue((project / 'written').is_file())


if __name__ == '__main__':
    unittest.main()
