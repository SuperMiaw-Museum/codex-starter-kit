"""Offline task tests with a private registry, fake Codex, and isolated terminals."""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import pty
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tty
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'bootstrap'


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bootstrap'
        shutil.copytree(SOURCE, self.bundle)
        self.registry = self.root / 'registry'
        path = self.bundle / 'tools/task_registry.py'
        code = path.read_text().replace("Path('/tmp') / f'codex-starter-kit-tasks-{os.getuid()}'",
                                        f'Path({str(self.registry)!r})')
        # Isolated subprocesses never send notices to real user terminals.
        code = code.replace("entries = os.listdir('/dev/pts')", 'entries = []')
        path.write_text(code)
        locking = self.bundle / 'tools/project_lock.py'
        lock_parent = tempfile.TemporaryDirectory()
        self.addCleanup(lock_parent.cleanup)
        self.lock_root = Path(lock_parent.name) / 'project-locks'
        locking.write_text(locking.read_text().replace(
            "Path('/tmp') / f'codex-starter-kit-project-locks-{os.getuid()}'",
            f'Path({str(self.lock_root)!r})'))
        self.test_runner = self.bundle / 'tools/run_codex.py'
        self.real_runner_source = self.test_runner.read_text()
        # Protocol tests only. The production entry point has no override.
        self.test_runner.write_text(self.real_runner_source.replace(
            '            project_lock.require_verified_execution_profile()',
            '            pass  # isolated protocol fixture'))
        self.env = dict(os.environ, HOME=str(self.root), NVM_DIR=str(self.root / 'nvm'))
        self.env.pop('CODEX_HOME', None)
        self.env.pop('CODEX_STARTER_KIT_SHOW_FULL_PROMPT', None)
        self.env.pop('CODEX_STARTER_KIT_NOTIFY_METADATA', None)
        self.env.pop('CODEX_STARTER_KIT_LIMIT_REMINDER', None)
        self.env.pop('CODEX_STARTER_KIT_LIVE_INSPECT', None)

    def run_task(self, *args):
        return subprocess.run([str(self.bundle / 'tools/manual-task.sh'), *args],
                              env=self.env, text=True, capture_output=True)

    def start(self, label='label'):
        result = self.run_task('start', 'project', label)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r'^[0-9a-f]{32}\n$')
        return result

    def state(self):
        return json.loads((self.registry / 'tasks.json').read_text())

    def registry_module(self):
        spec = importlib.util.spec_from_file_location('tasks', self.bundle / 'tools/task_registry.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def project_lock_module(self):
        spec = importlib.util.spec_from_file_location('project_lock', self.bundle / 'tools/project_lock.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def lock_runner(self, project, reservation=None):
        args = [str(self.bundle / 'tools/run-codex.sh')]
        if reservation is not None:
            args += ['--reservation-fd', str(reservation)]
        return args + [str(project), '--task', 'lock test']

    def fake_lock_codex(self, body):
        codex = self.root / 'codex'
        codex.write_text(f'#!{sys.executable}\n' + body)
        codex.chmod(0o755)
        self.env.update(PATH=f'{self.root}:{self.env["PATH"]}', CODEX_STARTER_KIT_NOTIFY_METADATA='0')

    def attempt_runner(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        receipt = Path(state.name) / 'receipt.json'
        runner, *args = self.lock_runner(self.root)
        return [runner, '--attempt-id', 'test-attempt', '--receipt', str(receipt), *args], receipt

    def test_unverified_profile_cannot_start_a_reserved_or_receipted_run(self):
        self.test_runner.write_text(self.real_runner_source)
        self.fake_lock_codex('raise SystemExit(99)\n')
        self.env['CODEX_STARTER_KIT_VERIFIED_PROFILE'] = 'workspace-write'
        command, receipt = self.attempt_runner()
        with self.project_lock_module().reserve_project(self.root) as fd:
            result = subprocess.run(self.lock_runner(self.root, fd), env=self.env,
                                    pass_fds=(fd,), capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('no verified isolation profile', result.stderr)
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('no verified isolation profile', result.stderr)
        self.assertNotIn('START Codex', result.stderr)
        self.assertEqual(list(receipt.parent.iterdir()), [])
        self.assertFalse(self.registry.exists())

    def test_runner_attempt_distinguishes_busy_from_codex_exit_75(self):
        self.fake_lock_codex('print("SANDBOX_HANDOFF_EXIT_CODE=0")\nraise SystemExit(75)\n')
        command, receipt = self.attempt_runner()
        with self.project_lock_module().reserve_project(self.root):
            started = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(started.returncode, 75, started.stderr)
        self.assertNotIn('START Codex', started.stderr)
        self.assertEqual(json.loads(receipt.read_bytes())['result']['kind'], 'busy')
        self.assertIsNone(json.loads(receipt.read_bytes())['result']['process_exit'])

        command, receipt = self.attempt_runner()
        with self.project_lock_module().reserve_project(self.root) as fd:
            command[5:5] = ['--reservation-fd', str(fd)]
            started = subprocess.run(command, env=self.env, pass_fds=(fd,),
                                     capture_output=True, text=True, timeout=5)
            retry = subprocess.run(command, env=self.env, pass_fds=(fd,),
                                   capture_output=True, text=True, timeout=5)
        self.assertEqual(started.returncode, 75, started.stderr)
        self.assertEqual(started.stderr.count('START Codex'), 1)
        self.assertEqual(started.stdout.count('ERROR Codex'), 1)
        record = json.loads(receipt.read_bytes())
        self.assertEqual((record['attempt_id'], record['project']), ('test-attempt', str(self.root)))
        self.assertEqual(record['result'], dict(kind='codex', exit_code=75, process_exit=75,
                                               signal=None, stage=None, capture_failed=False))
        self.assertEqual(retry.returncode, 2, retry.stderr)
        self.assertNotIn('START Codex', retry.stderr)
        self.assertEqual(json.loads(receipt.read_bytes()), record)

    def test_runner_attempt_reports_launch_failure(self):
        # An executable with an unavailable interpreter exists but exec fails.
        self.fake_lock_codex('')
        (self.root / 'codex').write_text('#!/nonexistent/test-interpreter\n')
        command, receipt = self.attempt_runner()
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertEqual(result.stderr.count('START Codex'), 1)
        self.assertEqual(result.stdout.count('ERROR Codex'), 1)
        record = json.loads(receipt.read_bytes())['result']
        self.assertEqual((record['kind'], record['stage'], record['process_exit']),
                         ('infrastructure_error', 'launch', None))
        self.assertEqual(self.state(), {})

    def test_runner_attempt_signal_and_crash_windows(self):
        self.fake_lock_codex(
            'import pathlib, time\n'
            'pathlib.Path("entered-codex").touch()\n'
            'deadline = time.monotonic() + 5\n'
            'while not pathlib.Path("release-codex").exists() and time.monotonic() < deadline:\n'
            '    time.sleep(.02)\n')
        module = self.project_lock_module()
        for sent in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=sent):
                for filename in ('entered-codex', 'release-codex'):
                    (self.root / filename).unlink(missing_ok=True)
                command, receipt = self.attempt_runner()
                with subprocess.Popen(command, env=self.env, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL) as runner:
                    try:
                        deadline = time.monotonic() + 4
                        while not (self.root / 'entered-codex').exists():
                            self.assertIsNone(runner.poll())
                            self.assertLess(time.monotonic(), deadline)
                            time.sleep(.02)
                        self.assertTrue(receipt.with_name('receipt.json.started').is_file())
                        self.assertFalse(receipt.exists())
                        runner.send_signal(sent)
                        expected = 143 if sent == signal.SIGTERM else -signal.SIGKILL
                        self.assertEqual(runner.wait(timeout=5), expected)
                        if sent == signal.SIGTERM:
                            result = json.loads(receipt.read_bytes())['result']
                            self.assertEqual((result['kind'], result['signal'], result['process_exit']),
                                             ('cancelled', signal.SIGTERM, -signal.SIGTERM))
                        else:
                            self.assertFalse(receipt.exists())
                            with self.assertRaises(module.ProjectBusy):
                                with module.reserve_project(self.root):
                                    self.fail('Codex still holds the project')
                    finally:
                        (self.root / 'release-codex').touch()
                        if runner.poll() is None:
                            runner.kill()
                            runner.wait(timeout=5)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with module.reserve_project(self.root):
                            break
                    except module.ProjectBusy:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.02)
                    except module.ProjectUnknown:
                        self.assertEqual(sent, signal.SIGKILL)
                        break

    def test_runner_crash_around_receipt_publication(self):
        self.fake_lock_codex('raise SystemExit(0)\n')
        state_module = self.bundle / 'tools/batch_state.py'
        source = state_module.read_text()
        write = '        write_state(self.receipt, dict(self.identity, result=asdict(result)), replace=False)'
        self.assertIn(write, source)
        barrier = ("        import time\n"
                   "        self.receipt.with_name('at-publication').touch()\n"
                   "        time.sleep(10)")
        for published in (False, True):
            with self.subTest(published=published):
                lines = (write, barrier) if published else (barrier, write)
                state_module.write_text(source.replace(write, '\n'.join(lines)))
                command, receipt = self.attempt_runner()
                with subprocess.Popen(command, env=self.env, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL) as runner:
                    try:
                        deadline = time.monotonic() + 5
                        while not receipt.with_name('at-publication').exists():
                            self.assertIsNone(runner.poll())
                            self.assertLess(time.monotonic(), deadline)
                            time.sleep(.02)
                        self.assertEqual(self.state(), {})
                        self.assertTrue(receipt.with_name('receipt.json.started').exists())
                        self.assertEqual(receipt.exists(), published)
                        if published:
                            self.assertEqual(json.loads(receipt.read_bytes())['result']['process_exit'], 0)
                    finally:
                        if runner.poll() is None:
                            runner.kill()
                            runner.wait(timeout=5)

    def test_project_reservation_spans_tasks_and_rejects_alias(self):
        self.fake_lock_codex('import sys\nsys.stdin.read()\n')
        project = self.root / 'project'
        project.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(project, target_is_directory=True)
        locking = self.project_lock_module()
        with locking.reserve_project(project) as reservation:
            for _ in range(2):
                blocked = subprocess.run(self.lock_runner(alias), env=self.env,
                                         capture_output=True, text=True, timeout=5)
                self.assertEqual(blocked.returncode, locking.BUSY_EXIT, blocked.stderr)
                self.assertIn('BUSY:', blocked.stderr)
                self.assertNotIn('START Codex', blocked.stderr)
                result = subprocess.run(self.lock_runner(alias, reservation), env=self.env,
                                        pass_fds=(reservation,), capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('SANDBOX_HANDOFF_EXIT_CODE=0', result.stdout)
            blocked = subprocess.run(self.lock_runner(project), env=self.env,
                                     capture_output=True, text=True, timeout=5)
            self.assertEqual(blocked.returncode, locking.BUSY_EXIT)
            rejected = subprocess.run(self.lock_runner(self.root, reservation), env=self.env,
                                      pass_fds=(reservation,), capture_output=True, text=True, timeout=5)
            self.assertEqual(rejected.returncode, 2)
        self.assertEqual(stat.S_IMODE(self.lock_root.stat().st_mode), 0o700)
        inodes = {path.name: path.stat().st_ino for path in self.lock_root.iterdir()}
        result = subprocess.run(self.lock_runner(project), env=self.env, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(inodes, {path.name: path.stat().st_ino for path in self.lock_root.iterdir()})

    def test_project_lock_requires_the_locked_open_description(self):
        self.fake_lock_codex('raise SystemExit(99)\n')
        locking = self.project_lock_module()
        with locking.reserve_project(self.root):
            path, = self.lock_root.glob('*.lock')
            with path.open('rb') as separately_opened:
                fd = separately_opened.fileno()
                result = subprocess.run(self.lock_runner(self.root, fd), env=self.env,
                                        pass_fds=(fd,), capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('does not hold an exclusive lock', result.stderr)
        path.chmod(0o644)
        result = subprocess.run(self.lock_runner(self.root), env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Unsafe project lock file', result.stderr)
        self.assertFalse(self.registry.exists())
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            with locking.reserve_project(self.lock_root.parent):
                self.fail('Project must not contain the lock directory')

    def test_concurrent_runners_cannot_share_a_project_or_reservation(self):
        self.fake_lock_codex(
            'import os, pathlib, time\n'
            'control = pathlib.Path(os.environ["LOCK_TEST_CONTROL"])\n'
            '(control / "ready").touch()\n'
            'while not (control / "released").exists():\n'
            '    time.sleep(.02)\n')
        self.env['LOCK_TEST_CONTROL'] = str(self.root)
        # Exercise the real checkout path with a fake launcher and isolated state.
        project = SOURCE.parent
        alias = self.root / 'relative-alias'
        alias.symlink_to(project, target_is_directory=True)
        locking = self.project_lock_module()

        def check(reservation):
            for name in ('ready', 'released'):
                (self.root / name).unlink(missing_ok=True)
            descriptors = () if reservation is None else (reservation,)
            with subprocess.Popen(self.lock_runner(project, reservation), env=self.env,
                                  pass_fds=descriptors, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) as original:
                try:
                    deadline = time.monotonic() + 5
                    while not (self.root / 'ready').exists():
                        self.assertIsNone(original.poll())
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.02)
                    blocked = subprocess.run(self.lock_runner(alias, reservation), env=self.env,
                                             pass_fds=descriptors, capture_output=True, text=True, timeout=5)
                    self.assertEqual(blocked.returncode, locking.BUSY_EXIT, blocked.stderr)
                    self.assertNotIn('START Codex', blocked.stderr)
                finally:
                    (self.root / 'released').touch()
                    try:
                        _, errors = original.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        original.kill()
                        original.communicate(timeout=5)
                        raise
                self.assertEqual(original.returncode, 0, errors)
                self.assertEqual(self.state(), {})

        check(None)
        with locking.reserve_project(project) as reservation:
            check(reservation)

    def test_project_lock_survives_reservation_owner_runner_and_codex_exit(self):
        self.fake_lock_codex(
            'import os, pathlib, time\n'
            'if pathlib.Path("released").exists():\n'
            '    raise SystemExit(0)\n'
            'pathlib.Path("leader").write_text(str(os.getpid()))\n'
            'if os.fork() == 0:\n'
            '    pathlib.Path("descendant").write_text(str(os.getpid()))\n'
            '    while not pathlib.Path("released").exists():\n'
            '        time.sleep(.02)\n'
            'else:\n'
            '    while not pathlib.Path("leader-exit").exists():\n'
            '        time.sleep(.02)\n'
            '    raise SystemExit(0)\n')
        launcher = (
            'import pathlib, subprocess, sys\n'
            'sys.path.insert(0, sys.argv[1])\n'
            'from project_lock import reserve_project\n'
            'project = pathlib.Path(sys.argv[2])\n'
            'with reserve_project(project) as fd:\n'
            '    runner = subprocess.Popen([sys.argv[3], "--reservation-fd", str(fd), str(project),'
            ' "--task", "survive"], pass_fds=(fd,))\n'
            '    (project / "runner").write_text(str(runner.pid))\n'
            '    runner.wait()\n')
        owner = subprocess.Popen([sys.executable, '-B', '-c', launcher, str(self.bundle / 'tools'),
                                  str(self.root), self.lock_runner(self.root)[0]], env=self.env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        runner_pid = None
        try:
            deadline = time.monotonic() + 5
            for name in ('runner', 'leader', 'descendant'):
                path = self.root / name
                while not path.exists() or not path.read_text():
                    self.assertIsNone(owner.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                if name == 'runner':
                    runner_pid = int(path.read_text())
            owner.kill()
            owner.wait(timeout=5)
            os.kill(runner_pid, signal.SIGKILL)
            for leader_alive in (True, False):
                if not leader_alive:
                    (self.root / 'leader-exit').touch()
                    leader = int((self.root / 'leader').read_text())
                    registry = self.registry_module()
                    while registry.identity(leader) is not None:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.02)
                blocked = subprocess.run(self.lock_runner(self.root), env=self.env,
                                         capture_output=True, timeout=5)
                self.assertEqual(blocked.returncode, 75, blocked.stderr)
            (self.root / 'released').touch()
            deadline = time.monotonic() + 5
            while True:
                result = subprocess.run(self.lock_runner(self.root), env=self.env,
                                        capture_output=True, timeout=5)
                if result.returncode != 75:
                    break
                self.assertLess(time.monotonic(), deadline)
            self.assertEqual(result.returncode, 76, result.stderr)
            self.assertIn(b'UNKNOWN:', result.stderr)
        finally:
            (self.root / 'leader-exit').touch()
            (self.root / 'released').touch()
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=5)
            if runner_pid is not None:
                # Let any surviving runner finish before the fixture disappears.
                deadline = time.monotonic() + 3
                registry = self.registry_module()
                while registry.identity(runner_pid) is not None and time.monotonic() < deadline:
                    time.sleep(.02)

    def test_direct_detached_descendant_without_fds_fences_project(self):
        self.fake_lock_codex(
            'import os, pathlib, time\n'
            'if os.fork():\n'
            '    raise SystemExit(0)\n'
            'os.setsid()\n'
            'os.closerange(3, 4096)\n'
            'for fd in (0, 1, 2):\n'
            '    os.close(fd)\n'
            'pathlib.Path("detached-ready").touch()\n'
            'deadline = time.monotonic() + 5\n'
            'while not pathlib.Path("detach-release").exists() and time.monotonic() < deadline:\n'
            '    time.sleep(.02)\n'
            'pathlib.Path("detached-done").touch()\n')
        command, receipt = self.attempt_runner()
        locking = self.project_lock_module()
        try:
            result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 76, result.stderr)
            self.assertTrue((self.root / 'detached-ready').exists())
            self.assertFalse((self.root / 'detached-done').exists())
            self.assertIn('project fenced', result.stderr)
            self.assertEqual(result.stdout.count('ERROR Codex'), 1)
            record = json.loads(receipt.read_bytes())['result']
            self.assertEqual((record['kind'], record['stage'], record['process_exit']),
                             ('unknown', 'quiescence', 0))
            with self.assertRaises(locking.ProjectUnknown):
                with locking.reserve_project(self.root):
                    self.fail('Detached subprocess can still write')
        finally:
            (self.root / 'detach-release').touch()
            deadline = time.monotonic() + 3
            while not (self.root / 'detached-done').exists() and time.monotonic() < deadline:
                time.sleep(.02)
        self.assertTrue((self.root / 'detached-done').exists())
        with self.assertRaises(locking.ProjectUnknown):
            with locking.reserve_project(self.root):
                self.fail('A later caller cannot independently prove quiescence')
        with patch.object(locking, 'boot_id', return_value=b'550e8400-e29b-41d4-a716-446655440000\n'):
            with locking.reserve_project(self.root):
                self.assertEqual(list(self.lock_root.glob('*.unknown')), [])

    def test_direct_crash_retains_fence_without_inherited_fds(self):
        self.fake_lock_codex(
            'import os, pathlib, time\n'
            'os.closerange(3, 4096)\n'
            'pathlib.Path("writer-ready").touch()\n'
            'deadline = time.monotonic() + 5\n'
            'while not pathlib.Path("release-writer").exists() and time.monotonic() < deadline:\n'
            '    time.sleep(.02)\n'
            'pathlib.Path("writer-done").touch()\n')
        command, receipt = self.attempt_runner()
        with subprocess.Popen(command, env=self.env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as runner:
            try:
                deadline = time.monotonic() + 4
                while not (self.root / 'writer-ready').exists():
                    self.assertIsNone(runner.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                runner.kill()
                runner.wait(timeout=5)
                self.assertFalse(receipt.exists())
                self.assertFalse((self.root / 'writer-done').exists())
                second, second_receipt = self.attempt_runner()
                blocked = subprocess.run(second, env=self.env, capture_output=True, text=True, timeout=5)
                self.assertEqual(blocked.returncode, 76, blocked.stderr)
                self.assertNotIn('START Codex', blocked.stderr)
                record = json.loads(second_receipt.read_bytes())['result']
                self.assertEqual((record['kind'], record['stage']), ('unknown', 'admission'))
            finally:
                (self.root / 'release-writer').touch()
                if runner.poll() is None:
                    runner.kill()
                    runner.wait(timeout=5)
                deadline = time.monotonic() + 3
                while not (self.root / 'writer-done').exists() and time.monotonic() < deadline:
                    time.sleep(.02)

    def test_direct_reaps_detached_child_before_reporting_success(self):
        self.fake_lock_codex(
            'import os, pathlib\n'
            'if os.fork():\n'
            '    raise SystemExit(0)\n'
            'os.setsid()\n'
            'os.closerange(3, 4096)\n'
            'pathlib.Path("detached-done").touch()\n')
        command, receipt = self.attempt_runner()
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'detached-done').exists())
        self.assertEqual(json.loads(receipt.read_bytes())['result']['kind'], 'codex')
        with self.project_lock_module().reserve_project(self.root):
            self.assertEqual(list(self.lock_root.glob('*.unknown')), [])

    def test_direct_allows_short_waitable_descendants_to_finish(self):
        self.fake_lock_codex(
            'import os, pathlib, time\n'
            'if os.fork():\n'
            '    raise SystemExit(0)\n'
            'os.setsid()\n'
            'os.closerange(3, 4096)\n'
            'time.sleep(1.25)\n'
            'pathlib.Path("late-exit").touch()\n')
        command, receipt = self.attempt_runner()
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=6)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'late-exit').exists())
        self.assertEqual(json.loads(receipt.read_bytes())['result']['kind'], 'codex')
        with self.project_lock_module().reserve_project(self.root):
            self.assertEqual(list(self.lock_root.glob('*.unknown')), [])

    @unittest.skipUnless(os.environ.get('CODEX_TEST_REAL_NPM') and os.environ.get('CODEX_TEST_REAL_NATIVE'),
                         'requires explicitly supplied local Codex launcher paths')
    def test_real_sandbox_launchers_and_fence(self):
        def describe_tree(pid, depth=0):
            base = Path(f'/proc/{pid}')
            try:
                name = (base / 'comm').read_text().strip()
                children = set()
                for thread in (base / 'task').iterdir():
                    try:
                        children.update((thread / 'children').read_text().split())
                    except FileNotFoundError:
                        continue
                held = []
                for entry in (base / 'fd').iterdir():
                    try:
                        target = os.readlink(entry)
                    except FileNotFoundError:
                        continue
                    if target.startswith(str(self.lock_root) + '/') and target.endswith(('.lock', '.active')):
                        held.append(target.rsplit('.', 1)[-1])
                print(f'{"  " * depth}{name} sid={os.getsid(pid)} flock-fds={sorted(held)}', flush=True)
            except OSError:
                return
            for child in sorted(children, key=int):
                describe_tree(int(child), depth + 1)

        for label, variable in (('npm', 'CODEX_TEST_REAL_NPM'), ('native', 'CODEX_TEST_REAL_NATIVE')):
            for crash in (False, True):
                with self.subTest(launcher=label, crash=crash):
                    project = self.root / f'{label}-{crash}'
                    project.mkdir()
                    launcher = Path(os.environ[variable]).absolute()
                    launcher_dir = Path(os.environ['CODEX_TEST_REAL_NPM']).absolute().parent
                    control = project / 'sandbox_probe.py'
                    control.write_text(
                        'import json, os, pathlib, time\n'
                        'fds = []\n'
                        'for fd in os.listdir("/proc/self/fd"):\n'
                        '    try:\n'
                        '        target = os.readlink("/proc/self/fd/" + fd)\n'
                        '    except FileNotFoundError:\n'
                        '        continue\n'
                        '    if target.endswith((".lock", ".active")) and "project-locks" in target:\n'
                        '        fds.append(target.rsplit(".", 1)[-1])\n'
                        'pathlib.Path("ready.tmp").write_text(json.dumps(sorted(fds)))\n'
                        'pathlib.Path("ready.tmp").replace("ready")\n'
                        'deadline = time.monotonic() + 6\n'
                        'while not pathlib.Path("release").exists() and time.monotonic() < deadline:\n'
                        '    time.sleep(.02)\n'
                        'pathlib.Path("done").touch()\n')
                    adapter = self.root / 'codex'
                    adapter.write_text('#!/bin/bash\nexec ' + shlex.join([
                        str(launcher), 'sandbox', '-P', ':workspace', '-C', str(project),
                        '--', sys.executable, '-B', str(control),
                    ]) + '\n')
                    adapter.chmod(0o755)
                    home = self.root / 'sandbox-home'
                    home.mkdir(exist_ok=True)
                    (home / '.codex').mkdir(exist_ok=True)
                    env = dict(self.env, HOME=str(home), CODEX_HOME=str(home / '.codex'),
                               PATH=os.pathsep.join([str(self.root), str(launcher_dir), self.env['PATH']]),
                               CODEX_STARTER_KIT_NOTIFY_METADATA='0')
                    locking = self.project_lock_module()
                    with subprocess.Popen(self.lock_runner(project), env=env, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, text=True) as runner:
                        try:
                            deadline = time.monotonic() + 5
                            while not (project / 'ready').exists():
                                if runner.poll() is not None:
                                    self.fail(runner.stderr.read())
                                self.assertLess(time.monotonic(), deadline)
                                time.sleep(.02)
                            fds = json.loads((project / 'ready').read_bytes())
                            print(f'Codex sandbox {label}: inherited flock descriptors={fds}', flush=True)
                            describe_tree(runner.pid)
                            if crash:
                                runner.kill()
                                runner.wait(timeout=5)
                                self.assertFalse((project / 'done').exists())
                                with self.assertRaises((locking.ProjectBusy, locking.ProjectUnknown)):
                                    with locking.reserve_project(project):
                                        self.fail('Sandbox process can still write')
                            (project / 'release').touch()
                            if not crash:
                                _, errors = runner.communicate(timeout=8)
                                self.assertEqual(runner.returncode, 0, errors)
                                with locking.reserve_project(project):
                                    self.assertTrue((project / 'done').exists())
                        finally:
                            (project / 'release').touch()
                            if runner.poll() is None:
                                runner.kill()
                                runner.wait(timeout=5)
                            deadline = time.monotonic() + 4
                            while not (project / 'done').exists() and time.monotonic() < deadline:
                                time.sleep(.02)
                    self.assertTrue((project / 'done').exists())
                    if crash:
                        deadline = time.monotonic() + 4
                        while True:
                            try:
                                with locking.reserve_project(project):
                                    self.fail('Crash fence must persist')
                            except locking.ProjectBusy:
                                self.assertLess(time.monotonic(), deadline)
                                time.sleep(.02)
                            except locking.ProjectUnknown:
                                break

    def test_registry_api_preserves_cwd_umask_and_cli_tokens(self):
        registry = self.registry_module()
        cwd = Path.cwd()
        old_mask = os.umask(0o777)
        try:
            with patch.object(os, 'umask', side_effect=AssertionError('changed process umask')):
                self.assertIsNone(registry.status())
                self.assertFalse(self.registry.exists())
                token = registry.start('project', 'from Python')
                active, stale_count = registry.status()
                self.assertEqual((active[0]['label'], stale_count), ('from Python', 0))
                for name, mode in (('.', 0o700), ('lock', 0o600), ('tasks.json', 0o600)):
                    self.assertEqual(stat.S_IMODE((self.registry / name).stat().st_mode), mode)
                self.assertEqual(Path.cwd(), cwd)
                self.assertEqual(self.run_task('end', token, '0').returncode, 0)
                token = self.start('from CLI').stdout.strip()
                registry.end(token, 0)
                self.assertEqual(registry.status(), ([], 0))
                with self.assertRaises(registry.UnknownTask):
                    registry.end(token, 0)
                self.assertEqual(Path.cwd(), cwd)
        finally:
            current_mask = os.umask(old_mask)
        self.assertEqual(current_mask, 0o777)

    def test_manual_cli_preserves_usage_and_rejects_extended_arguments(self):
        for args in (('start', 'p', 'l', str(os.getpid())),
                     ('end', 'a' * 32, '0', 'metadata'), ('status', 'extra')):
            result = self.run_task(*args)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (2, '', ''))
        result = self.run_task()
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertTrue(result.stderr.startswith('Usage: manual-task.sh '))
        self.assertFalse(self.registry.exists())

    def test_format_validation_and_end_failure(self):
        start = self.start('hello\n\x1b[31m' + 'x' * 200)
        self.assertIn('🤖 1 🚀 START ChatGPT [project] — hello ', start.stderr)
        self.assertNotIn('\x1b', start.stderr)
        token = start.stdout.strip()
        self.assertEqual(self.run_task('end', token, '256').returncode, 2)
        self.assertEqual(len(self.state()), 1)
        result = self.run_task('end', token, '7')
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, r'🤖 0 ❌ ERROR ChatGPT \[project\] \(\d+s\)')
        self.assertEqual(self.run_task('end', token, '0').returncode, 3)
        self.assertIn('🤖 0 active tasks', self.run_task('status').stdout)

    def test_manual_notice_preserves_absolute_path_and_format(self):
        project = str(self.root / 'project with spaces')
        started = self.run_task('start', project, 'Task description')
        self.assertEqual(started.returncode, 0, started.stderr)
        timestamp = r'\[\d{2}:\d{2}:\d{2}\]'
        path = re.escape(project)
        self.assertRegex(started.stderr,
                         rf'^{timestamp} 🤖 1 🚀 START ChatGPT \[{path}\] — Task description\n\Z')
        token = started.stdout.strip()
        self.assertEqual(self.state()[token]['mode'], 'Manual')
        ended = self.run_task('end', token, '0')
        self.assertEqual(ended.returncode, 0, ended.stderr)
        self.assertRegex(ended.stdout, rf'^{timestamp} 🤖 0 ✅ END ChatGPT \[{path}\] \(\d+s\)\n\Z')

    def test_manual_full_prompt_format(self):
        self.env['CODEX_STARTER_KIT_SHOW_FULL_PROMPT'] = '1'
        label = 'Short manual task\nwith details'
        result = self.start(label)
        self.assertRegex(result.stderr, r'🚀 START ChatGPT \[project\]\n$')
        self.assertNotIn(label, result.stderr)
        self.assertEqual(self.state()[result.stdout.strip()]['label'], 'Short manual task with details')
        self.assertEqual(self.run_task('end', result.stdout.strip(), '0').returncode, 0)

    def test_codex_inspection_command_in_start_notice(self):
        command = ('tmux -S /tmp/codex-inspect.Ab12Cd34/socket '
                   'attach -r -t codex-inspect-Ab12Cd34')
        self.env['CODEX_STARTER_KIT_INSPECT_COMMAND'] = command
        result = subprocess.run(
            [str(self.bundle / 'tools/notify-task-done.sh'),
             'start', 'project', 'task details', str(os.getpid())],
            env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        token = result.stdout.strip()
        summary, inspection = result.stderr.splitlines()
        self.assertTrue(summary.endswith(f' | task: {token[:12]}'))
        self.assertEqual(inspection, 'Read-only inspection: ' + command)
        self.assertEqual(self.run_task('end', token, '0').returncode, 0)

    def test_codex_notices_correlate_overlapping_tasks_in_same_project(self):
        script = str(self.bundle / 'tools/notify-task-done.sh')
        tokens = []
        for _ in range(2):
            start = subprocess.run([script, 'start', 'project', 'same task', str(os.getpid())],
                                   env=self.env, text=True, capture_output=True)
            self.assertEqual(start.returncode, 0, start.stderr)
            token = start.stdout.strip()
            self.assertTrue(start.stderr.endswith(f' | task: {token[:12]}\n'))
            tokens.append(token)
        self.assertNotEqual(tokens[0][:12], tokens[1][:12])

        for token in reversed(tokens):
            end = self.run_task('end', token, '0')
            self.assertEqual(end.returncode, 0, end.stderr)
            self.assertTrue(end.stdout.endswith(f' | task: {token[:12]}\n'))
        self.assertEqual(self.state(), {})

    def test_status_readable_truncation(self):
        self.env['TZ'] = 'EST5'
        self.start('A short\ntask')
        self.start('x' * 120)
        self.start('y' * 121)
        codex = subprocess.run(
            [str(self.bundle / 'tools/notify-task-done.sh'), 'start', 'other', 'Codex task', str(os.getpid())],
            env=self.env, text=True, capture_output=True)
        self.assertEqual(codex.returncode, 0, codex.stderr)

        # Interpret the recorded timestamps in the same fixed local timezone.
        local_zone = timezone(timedelta(hours=-5))
        dates = [datetime.fromtimestamp(task['started'], local_zone).strftime('%Y-%m-%d %H:%M')
                 for task in self.state().values()]
        status = self.run_task('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout, (
            '🤖 4 active tasks (0 stale)\n\n'
            f'{dates[0]} Manual [project]\n  A short task\n'
            f'{dates[1]} Manual [project]\n  {"x" * 120}\n'
            f'{dates[2]} Manual [project]\n  {"y" * 120}...\n'
            f'{dates[3]} Codex [other]\n  Codex task\n'))
        self.env['CODEX_STARTER_KIT_SHOW_FULL_PROMPT'] = '1'
        self.assertEqual(self.run_task('status').stdout, status.stdout)

    def test_concurrent_start_end_and_duplicate(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            starts = list(pool.map(lambda _: self.start(), range(20)))
            counts = sorted(int(r.stderr.split('🤖 ')[1].split()[0]) for r in starts)
            self.assertEqual(counts, list(range(1, 21)))
            tokens = [r.stdout.strip() for r in starts]
            self.assertEqual(len(set(tokens)), 20)
            ends = list(pool.map(lambda t: self.run_task('end', t, '0'), tokens[:10]))
            self.assertEqual(sorted(int(r.stdout.split('🤖 ')[1].split()[0]) for r in ends), list(range(10, 20)))
            self.assertEqual(set(self.state()), set(tokens[10:]))
            duplicates = list(pool.map(lambda _: self.run_task('end', tokens[10], '0'), range(6)))
            self.assertEqual(sorted(r.returncode for r in duplicates), [0, 3, 3, 3, 3, 3])
            self.assertEqual(len(self.state()), 9)

    def test_stale_status_readonly_and_cleanup(self):
        token = self.start().stdout.strip()
        state = self.state()
        state[token]['started'] = 0
        path = self.registry / 'tasks.json'
        path.write_text(json.dumps(state))
        before = path.read_bytes()
        self.assertIn('0 active tasks (1 stale)', self.run_task('status').stdout)
        self.assertEqual(path.read_bytes(), before)
        result = self.start()
        self.assertIn('🤖 1 🚀 START', result.stderr)
        self.assertNotIn(token, self.state())
        state = self.state()
        state[result.stdout.strip()].update(mode='Codex', pid=999999999, identity=['old', '1'])
        path.write_text(json.dumps(state))
        self.assertIn('0 active tasks (1 stale)', self.run_task('status').stdout)
        self.assertIn('🤖 1 🚀 START', self.start().stderr)

    def test_mixed_transitions_deliver_in_commit_order(self):
        # Record what delivery sees while the cross-process lock is held.
        path = self.bundle / 'tools/task_registry.py'
        path.write_text(path.read_text().replace(
            '    # Broken stdout',
            "    with (ROOT / 'events.jsonl').open('a') as log:\n"
            "        count = len(json.loads((ROOT / 'tasks.json').read_text()))\n"
            "        log.write(json.dumps([phase, count, message]) + '\\n')\n"
            '    # Broken stdout'))
        tokens = [self.start().stdout.strip() for _ in range(10)]
        def transition(index):
            if index % 2:
                return self.run_task('end', tokens[index // 2], '0')
            # A real, live process identity makes this a Codex task.
            return subprocess.run(
                [str(self.bundle / 'tools/notify-task-done.sh'), 'start', 'p', 'l', str(os.getpid())],
                env=self.env, text=True, capture_output=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(transition, range(20)))
        self.assertTrue(all(r.returncode == 0 for r in results))
        count = 0
        events = (self.registry / 'events.jsonl').read_text().splitlines()
        self.assertEqual(len(events), 30)
        for line in events:
            phase, committed_count, message = json.loads(line)
            count += 1 if phase == 'START' else -1
            self.assertEqual(committed_count, count)
            self.assertIn(f'🤖 {count} ', message)
        self.assertEqual(len(self.state()), 10)
        self.assertTrue(all(t['mode'] == 'Codex' for t in self.state().values()))

    def test_corrupt_entries_reject_end_without_mutation(self):
        token = self.start().stdout.strip()
        original = self.state()
        path = self.registry / 'tasks.json'
        for fields in ({'started': float('nan')}, {'started': True},
                       {'mode': 'Codex', 'pid': 1, 'identity': []}):
            state = {token: dict(original[token], **fields)}
            path.write_text(json.dumps(state))
            before = path.read_bytes()
            result = self.run_task('end', token, '0')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertEqual(path.read_bytes(), before)

    def test_deeply_nested_registry_keeps_runner_best_effort(self):
        self.start()
        path = self.registry / 'tasks.json'
        malformed = b'[' * 10000 + b'0' + b']' * 10000
        path.write_bytes(malformed)
        status = self.run_task('status')
        self.assertEqual(status.returncode, 2, status.stderr)
        self.assertNotIn('Traceback', status.stderr)

        codex = self.root / 'codex'
        codex.write_text('#!/bin/bash\ncat >/dev/null\nexit 7\n')
        codex.chmod(0o755)
        result = subprocess.run(
            [str(self.bundle / 'tools/run-codex.sh'), str(self.root), '--task', 'run anyway'],
            env=dict(self.env, PATH=f'{self.root}:{self.env["PATH"]}',
                     CODEX_STARTER_KIT_NOTIFY_METADATA='0'),
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, 'SANDBOX_HANDOFF_EXIT_CODE=7\n')
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(path.read_bytes(), malformed)

    def test_nonregular_notice_logs_do_not_block_end(self):
        notifier = str(self.bundle / 'tools/notify-task-done.sh')
        home = self.root / '.codex'
        directory = home / 'sessions/2026/11/22'
        directory.mkdir(parents=True)
        session = '01234567-89ab-4def-8123-0123456789ab'
        os.mkfifo(directory / f'rollout-test-{session}.jsonl')
        metadata = self.root / 'metadata.jsonl'
        metadata.write_text(json.dumps({'session': session}) + '\n')
        fifo = self.root / 'metadata.fifo'
        os.mkfifo(fifo)
        for path in (fifo, metadata):
            with self.subTest(path=path.name):
                start = subprocess.run([notifier, 'start', 'p', 'task', str(os.getpid())],
                                       env=self.env, capture_output=True, text=True, timeout=5)
                self.assertEqual(start.returncode, 0, start.stderr)
                end = subprocess.run([notifier, 'end', start.stdout.strip(), '0', str(path)],
                                     env=self.env, capture_output=True, text=True, timeout=2)
                self.assertEqual(end.returncode, 0, end.stderr)
                self.assertIn('✅ END Codex', end.stdout)
                if path == metadata:
                    self.assertIn(f'session: {session}', end.stdout)
                self.assertEqual(self.state(), {})

    def test_end_with_ascii_stdout_does_not_report_registry_failure(self):
        token = self.start().stdout.strip()
        self.env['PYTHONIOENCODING'] = 'ascii'
        result = self.run_task('end', token, '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state(), {})

    def test_reused_pid_and_stale_end(self):
        token = self.start().stdout.strip()
        state = self.state()
        state[token].update(mode='Codex', pid=os.getpid(), identity=['old-boot', '1'])
        (self.registry / 'tasks.json').write_text(json.dumps(state))
        self.assertIn('0 active tasks (1 stale)', self.run_task('status').stdout)
        # An END can still finish a stale token until another transition prunes it.
        result = self.run_task('end', token, '0')
        self.assertEqual(result.returncode, 0)
        self.assertIn('🤖 0 ✅ END Codex', result.stdout)

    def test_unsafe_paths_and_atomic_failure(self):
        target = self.root / 'target'
        target.mkdir()
        self.registry.symlink_to(target)
        self.assertEqual(self.run_task('start', 'p', 'l').returncode, 2)
        self.assertEqual(list(target.iterdir()), [])
        self.registry.unlink()
        token = self.start().stdout.strip()
        other = self.start().stdout.strip()
        # A write failure must preserve the original task for retry.
        module = self.registry_module()
        cwd = os.getcwd()
        with patch.object(module.os, 'replace', side_effect=OSError('write failed')):
            with patch.object(module, 'deliver') as delivered:
                with self.assertRaises(OSError):
                    module.end(token, 0, notify=delivered)
                delivered.assert_not_called()
        self.assertEqual(os.getcwd(), cwd)
        self.assertIn(token, self.state())
        self.assertEqual(self.run_task('end', token, '0').returncode, 0)
        self.assertEqual(set(self.state()), {other})

    def test_runner_stdin_nvm_exit_and_shared_count(self):
        self.start()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        codex = bin_dir / 'codex'
        codex.write_text('#!/bin/bash\n[[ $NVM_OK == yes ]] || exit 99\nprintf "ARGS:%s\\n" "$*"\ncat\nexit 7\n')
        codex.chmod(0o755)
        nvm = self.root / 'nvm'
        nvm.mkdir()
        (nvm / 'nvm.sh').write_text(f'export NVM_OK=yes\nexport PATH="{bin_dir}:$PATH"\n')
        task = self.root / 'task.txt'
        task.write_text('a task\nsecond line\n')
        result = subprocess.run([str(self.bundle / 'tools/run-codex.sh'), str(self.root), str(task)],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn('🤖 2 🚀 START Codex', result.stderr)
        self.assertIn('🤖 1 ❌ ERROR Codex', result.stdout)
        self.assertIn('SANDBOX_HANDOFF_EXIT_CODE=7', result.stdout)
        self.assertNotIn('SANDBOX_HANDOFF_TMUX_SESSION', result.stdout)
        self.assertIn('a task\nsecond line\n', result.stdout)
        self.assertIn('ARGS:exec --skip-git-repo-check -', result.stdout)
        self.assertEqual(len(self.state()), 1)

    def test_live_inspection_requires_tmux(self):
        for name in ('bash', 'dirname', 'python3'):
            (self.root / name).symlink_to(shutil.which(name))
        codex = self.root / 'codex'
        codex.write_text('#!/bin/bash\nexit 99\n')
        codex.chmod(0o755)
        result = subprocess.run(
            [str(self.bundle / 'tools/run-codex.sh'), str(self.root), '--task', 'inspect'],
            env=dict(self.env, PATH=str(self.root), CODEX_STARTER_KIT_LIVE_INSPECT='1'),
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 127)
        self.assertIn('CODEX_STARTER_KIT_LIVE_INSPECT=1 requires tmux in PATH', result.stderr)
        self.assertNotIn('START Codex', result.stderr)

    @unittest.skipUnless(shutil.which('tmux'), 'tmux is required for the live integration test')
    def test_live_inspection_shows_json_progress_and_preserves_resume_metadata(self):
        session = '01234567-89ab-4def-8123-0123456789ab'
        codex = self.root / 'codex'
        events = [
            {'type': 'thread.started', 'thread_id': session},
            {'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'test command'}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'aggregated_output': 'live output'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}},
        ]
        codex.write_text(
            '#!/bin/bash\n'
            'if [[ $* == "exec resume --help" ]]; then echo "  --json"; exit; fi\n'
            f'[[ $* == "exec resume --skip-git-repo-check --json {session} -" ]] || exit 99\n'
            '[[ $(cat) == "resume task" ]] || exit 98\n'
            "cat <<'EVENTS'\n" + '\n'.join(map(json.dumps, events)) + '\nEVENTS\n'
            'echo "live stderr" >&2\n'
            'while [[ ! -f release ]]; do sleep 0.05; done\n'
            'exit 7\n')
        codex.chmod(0o755)
        env = dict(self.env, PATH=f'{self.root}:{self.env["PATH"]}',
                   CODEX_STARTER_KIT_LIVE_INSPECT='1')
        args = [str(self.bundle / 'tools/run-codex.sh'), str(self.root),
                '--resume', session, '--task', 'resume task']
        # Use files so a failure to publish the attachment command cannot block a read.
        with (self.root / 'stdout').open('w+') as output, (self.root / 'stderr').open('w+') as errors:
            with subprocess.Popen(args, env=env, stdout=output, stderr=errors) as runner:
                try:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        log = (self.root / 'stdout').read_text()
                        match = re.search(r'tmux -S (\S+) attach -r -t (\S+)', log)
                        if match:
                            socket, tmux_session = match.groups()
                            pane = subprocess.run(
                                ['tmux', '-S', socket, 'capture-pane', '-p', '-t', tmux_session],
                                capture_output=True, text=True, timeout=2)
                            if 'live output' in pane.stdout and 'live stderr' in pane.stdout:
                                break
                        if runner.poll() is not None:
                            break
                        time.sleep(0.05)
                    else:
                        self.fail('Timed out waiting for live Codex output')
                    self.assertIsNone(runner.poll(), (self.root / 'stderr').read_text())
                    self.assertIn(f'SANDBOX_HANDOFF_TMUX_SESSION={tmux_session}', log)
                    self.assertNotEqual(tmux_session, session)
                    self.assertIn('test command', pane.stdout)
                    self.assertEqual(len(self.state()), 1)
                finally:
                    (self.root / 'release').touch()
                    try:
                        runner.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        runner.terminate()
                        runner.wait(timeout=5)
        self.assertEqual(runner.returncode, 7)
        log = (self.root / 'stdout').read_text()
        self.assertIn(f'SANDBOX_HANDOFF_SESSION_ID={session}', log)
        self.assertIn('SANDBOX_HANDOFF_EXIT_CODE=7', log)
        self.assertIn('❌ ERROR Codex', log)
        self.assertIn(f'tokens: 15 | session: {session}', log)
        summary, inspection = (self.root / 'stderr').read_text().splitlines()
        self.assertIn('START Codex', summary)
        self.assertTrue(summary.endswith(f' | resume: {session}'))
        self.assertEqual(inspection, f'Read-only inspection: tmux -S {socket} attach -r -t {tmux_session}')
        self.assertEqual(self.state(), {})
        self.assertFalse(Path(socket).parent.exists())

    def test_runner_rejects_invalid_resume_arguments(self):
        session = '01234567-89ab-4def-8123-0123456789ab'
        runner = self.bundle / 'tools/run-codex.sh'
        for args in (('--resume',), ('--resume', session), ('--resume', session, '--task'),
                     ('--resume', '--last', '--task', 'text'),
                     ('--resume', 'invalid-id', '--task', 'text'),
                     ('--resume', session, '--resume', session, '--task', 'text'),
                     ('--resume', session, '--task', 'text', 'extra'),
                     ('--resume', session, '--task', '')):
            with self.subTest(args=args):
                result = subprocess.run([str(runner), str(self.root), *args],
                                        env=self.env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn('START Codex', result.stderr)
                self.assertNotIn('invalid-id', result.stderr)
                self.assertEqual(result.stdout, '')
        self.assertFalse(self.registry.exists())

    def test_runner_resume_uses_stdin_and_captures_actual_session(self):
        session = '01234567-89ab-4def-8123-0123456789ab'
        requested = 'fedcba98-7654-4321-8765-43210fedcba9'
        task = '- continue this task\nsecond line'
        task_file = self.root / 'task.txt'
        task_file.write_text(task + '\n')
        codex = self.root / 'codex'
        codex.write_text(
            '#!/bin/bash\n'
            'if [[ $* == "exec resume --help" ]]; then echo "  --json"; exit; fi\n'
            'printf "%s\\n" "$*" >> "$HOME/invocations"\n'
            'cat > "$HOME/prompt"\n'
            'if [[ $CODEX_RESULT != 0 ]]; then\n'
            """  echo '{"type":"error","message":"Session unavailable"}'\n"""
            '  exit "$CODEX_RESULT"\n'
            'fi\n'
            """echo '{"type":"thread.started","thread_id":"invalid"}'\n"""
            """printf '{"type":"thread.started","thread_id":"%s"}\\n' "$ACTUAL_SESSION"\n"""
        )
        codex.chmod(0o755)
        self.env.update(PATH=f'{self.root}:{self.env["PATH"]}', ACTUAL_SESSION=session)
        runner = self.bundle / 'tools/run-codex.sh'
        invocations = self.root / 'invocations'
        # Keep the requested UUID separate from the UUID reported by Codex.
        for code, task_args in ((0, ['--task', task]), (0, [str(task_file)]),
                                (9, ['--task', task])):
            with self.subTest(code=code, task_args=task_args):
                invocations.write_text('')
                result = subprocess.run(
                    [str(runner), str(self.root), '--resume', requested.upper(), *task_args],
                    env=dict(self.env, CODEX_RESULT=str(code)),
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(invocations.read_text(),
                                 f'exec resume --skip-git-repo-check --json {requested.upper()} -\n')
                self.assertEqual((self.root / 'prompt').read_text(), task + '\n')
                self.assertIn(f'SANDBOX_HANDOFF_EXIT_CODE={code}', result.stdout)
                task_id = re.search(r' \| task: ([0-9a-f]{12}) \| resume: ' + requested + r'$',
                                    result.stderr.splitlines()[0])
                self.assertIsNotNone(task_id, result.stderr)
                self.assertIn(f' | task: {task_id[1]}', result.stdout)
                if code:
                    self.assertIn('Session unavailable', result.stderr)
                    self.assertIn('ERROR Codex', result.stdout)
                    self.assertNotIn('SANDBOX_HANDOFF_SESSION_ID=', result.stdout)
                    self.assertNotIn(' | session:', result.stdout)
                else:
                    self.assertIn(f'SANDBOX_HANDOFF_SESSION_ID={session}\n', result.stdout)
                    self.assertIn(f' | session: {session}\n', result.stdout)
                self.assertEqual(self.state(), {})

        # Session recovery uses the task output even when the registry is unavailable.
        (self.registry / 'tasks.json').write_text('corrupt')
        result = subprocess.run(
            [str(runner), str(self.root), '--resume', session, str(task_file)],
            env=dict(self.env, CODEX_RESULT='0'), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         f'SANDBOX_HANDOFF_SESSION_ID={session}\nSANDBOX_HANDOFF_EXIT_CODE=0\n')

    def test_runner_optional_full_prompt(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        os.set_blocking(master, False)
        entry = Path(os.ttyname(slave)).name
        registry_script = self.bundle / 'tools/task_registry.py'
        registry_script.write_text(registry_script.read_text().replace('entries = []', f'entries = [{entry!r}]'))
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        codex = bin_dir / 'codex'
        codex.write_text('#!/bin/bash\ncp "$REGISTRY/tasks.json" "$HOME/active.json"\nprintf "Codex started\\n"\ncat\nexit 7\n')
        codex.chmod(0o755)
        self.env['PATH'] = str(bin_dir) + ':' + self.env['PATH']
        self.env['REGISTRY'] = str(self.registry)
        task = '- first line with literal \\n and %s\n\n  second line ' + 'x' * 300 + '\\'
        task_file = self.root / 'task.txt'
        task_file.write_text(task + '\n\n')
        for setting, task_args in ((None, ['--task', task]), ('0', [str(task_file)]),
                                   ('1', ['--task', task]), ('1', [str(task_file)])):
            with self.subTest(setting=setting, from_file=len(task_args) == 1):
                env = self.env.copy()
                if setting is not None:
                    env['CODEX_STARTER_KIT_SHOW_FULL_PROMPT'] = setting
                result = subprocess.run(
                    [str(self.bundle / 'tools/run-codex.sh'), str(self.root), *task_args],
                    env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 7, result.stderr)
                preview = '' if setting == '1' else ' ' + task.replace('\n', ' ')[:120] + '...'
                self.assertRegex(
                    result.stderr,
                    rf'^\[\d{{2}}:\d{{2}}:\d{{2}}\] 🤖 1 🚀 START Codex '
                    rf'\[{re.escape(self.root.name)}\]{re.escape(preview)} \| task: [0-9a-f]{{12}}\n\Z')
                display, marker, output = result.stdout.partition('Codex started\n')
                self.assertEqual(display, '')
                self.assertEqual(marker, 'Codex started\n')
                self.assertTrue(output.startswith(task + '\n'))
                self.assertRegex(output[len(task) + 1:],
                                 r'^\[\d{2}:\d{2}:\d{2}\] 🤖 0 ❌ ERROR Codex '
                                 r'\[[^\n]+\] duration: \d+s \| task: [0-9a-f]{12}\n'
                                 r'SANDBOX_HANDOFF_EXIT_CODE=7\n\Z')
                start = f'\r\n\x1b[1;36m{result.stderr.rstrip(chr(10))}\x1b[0m\r\n'.encode()
                if setting == '1':
                    start += task.encode() + (b'\n\n' if len(task_args) == 1 else b'\r\n')
                end = output[len(task) + 1:].splitlines()[0]
                self.assertEqual(os.read(master, 4096), start + f'\r\n\a\x1b[1;31m{end}\x1b[0m\r\n'.encode())
                active = json.loads((self.root / 'active.json').read_text())
                registered_task, = active.values()
                self.assertEqual(set(registered_task), {'started', 'project', 'label', 'mode', 'pid', 'identity'})
                self.assertEqual(registered_task['label'], task.replace('\n', ' ')[:120] + '...')
                self.assertEqual(self.state(), {})

    def test_runner_start_preview_length(self):
        codex = self.root / 'codex'
        codex.write_text('#!/bin/bash\ncat >/dev/null\n')
        codex.chmod(0o755)
        self.env['PATH'] = f'{self.root}:{self.env["PATH"]}'
        for task, preview in (('short\ntext', 'short text'),
                              ('x' * 120, 'x' * 120),
                              ('x' * 121, 'x' * 120 + '...')):
            with self.subTest(length=len(task)):
                result = subprocess.run(
                    [str(self.bundle / 'tools/run-codex.sh'), str(self.root), '--task', task],
                    env=self.env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(
                    result.stderr,
                    rf'^\[\d{{2}}:\d{{2}}:\d{{2}}\] 🤖 1 🚀 START Codex '
                    rf'\[{re.escape(self.root.name)}\] {re.escape(preview)} \| task: [0-9a-f]{{12}}\n\Z')

    def test_runner_signal_and_notification_failure(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        codex = bin_dir / 'codex'
        codex.write_text('#!/bin/bash\nkill -TERM "$PPID"\nexit 0\n')
        codex.chmod(0o755)
        self.env['PATH'] = str(bin_dir) + ':' + self.env['PATH']
        result = subprocess.run([str(self.bundle / 'tools/run-codex.sh'), str(self.root),
                                 '--task', 'signal check'], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn('SANDBOX_HANDOFF_EXIT_CODE=143', result.stdout)
        self.assertIn('🤖 0 ❌ ERROR Codex', result.stdout)
        self.assertEqual(self.state(), {})
        # Registry rejection is best effort for the runner; task still executes.
        (self.registry / 'tasks.json').write_text('corrupt')
        codex.write_text('#!/bin/bash\ncat\nexit 9\n')
        result = subprocess.run([str(self.bundle / 'tools/run-codex.sh'), str(self.root),
                                 '--task', 'still runs'], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 9)
        self.assertIn('still runs', result.stdout)
        self.assertIn('SANDBOX_HANDOFF_EXIT_CODE=9', result.stdout)

    def test_runner_large_relative_task_file_with_cdpath(self):
        project = self.root / 'project with spaces'
        project.mkdir()
        body = ('Inspect this task.\n' + 'x' * (512 * 1024)).encode()
        (self.root / 'task.txt').write_bytes(body + b'\n\n')
        codex = self.root / 'codex'
        codex.write_text('#!/bin/bash\ncat > received\n')
        codex.chmod(0o755)
        result = subprocess.run(
            ['bootstrap/tools/run-codex.sh', project.name, '--task-file', 'task.txt'],
            cwd=self.root,
            env=dict(self.env, PATH=f'{self.root}:{self.env["PATH"]}', CDPATH=str(self.root)),
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((project / 'received').read_bytes(), body + b'\n')
        self.assertEqual(result.stderr.count('START Codex'), 1)
        self.assertEqual(result.stdout.count('END Codex'), 1)
        self.assertEqual(result.stdout.count('SANDBOX_HANDOFF_EXIT_CODE=0'), 1)
        self.assertEqual(self.state(), {})

    def test_runner_parser_failure_drains_stdout_and_keeps_codex_status(self):
        parser = self.bundle / 'tools/codex_notice.py'
        parser.write_text(parser.read_text().replace('capture(*sys.argv[1:])', 'sys.exit(23)'))
        codex = self.root / 'codex'
        codex.write_text(f'#!{sys.executable}\n'
                         'import pathlib, sys\n'
                         'if "--help" in sys.argv:\n'
                         '    print("  --json")\n'
                         '    sys.exit()\n'
                         'sys.stdout.buffer.write(b"x" * (1024 * 1024))\n'
                         'sys.stdout.buffer.flush()\n'
                         'pathlib.Path("completed").touch()\n')
        codex.chmod(0o755)
        for live in ('0', '1') if shutil.which('tmux') else ('0',):
            with self.subTest(live=live):
                (self.root / 'completed').unlink(missing_ok=True)
                result = subprocess.run(
                    [str(self.bundle / 'tools/run-codex.sh'), str(self.root), '--task', 'test'],
                    env=dict(self.env, PATH=f'{self.root}:{self.env["PATH"]}',
                             CODEX_STARTER_KIT_LIVE_INSPECT=live),
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.root / 'completed').exists())
                self.assertIn('Codex output capture failed: parser exited with status 23', result.stderr)
                self.assertEqual(result.stdout.count('SANDBOX_HANDOFF_EXIT_CODE=0'), 1)
                self.assertEqual(result.stdout.count('END Codex'), 1)
                self.assertEqual(self.state(), {})

    @unittest.skipUnless(shutil.which('tmux'), 'tmux is required for the live integration test')
    def test_live_inspection_preserves_fast_exit_and_session_marker(self):
        project = self.root / 'project with spaces'
        project.mkdir()
        session = '01234567-89ab-4def-8123-0123456789ab'
        codex = self.root / 'codex'
        codex.write_text(
            '#!/bin/bash\n'
            'if [[ $2 == --help ]]; then echo "  --json"; exit; fi\n'
            '[[ $(cat) == "quick task" ]] || exit 99\n'
            '[[ ${PWD##*/} == "project with spaces" ]] || exit 98\n'
            f"echo '{{\"type\":\"thread.started\",\"thread_id\":\"{session}\"}}'\n"
            'exit 7\n')
        codex.chmod(0o755)
        result = subprocess.run(
            [str(self.bundle / 'tools/run-codex.sh'), str(project), '--task', 'quick task'],
            env=dict(self.env, PATH=f'{self.root}:{self.env["PATH"]}',
                     CODEX_STARTER_KIT_LIVE_INSPECT='1'),
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stderr.count('START Codex'), 1)
        self.assertEqual(result.stdout.count(f'SANDBOX_HANDOFF_SESSION_ID={session}'), 1)
        self.assertEqual(result.stdout.count('ERROR Codex'), 1)
        self.assertTrue(result.stdout.endswith('SANDBOX_HANDOFF_EXIT_CODE=7\n'))
        socket = re.search(r'tmux -S (\S+) attach -r', result.stdout)[1]
        self.assertFalse(Path(socket).parent.exists())
        self.assertEqual(self.state(), {})

    def test_runner_cancels_persistent_descendants(self):
        codex = self.root / 'codex'
        codex.write_text(f'#!{sys.executable}\n'
                         'import os, pathlib, signal, time\n'
                         'for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):\n'
                         '    signal.signal(number, signal.SIG_IGN)\n'
                         'pid = os.fork()\n'
                         'if not pid:\n'
                         '    pathlib.Path("child-pid").write_text(str(os.getpid()))\n'
                         'while True:\n'
                         '    time.sleep(1)\n')
        codex.chmod(0o755)
        cases = [('0', number) for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)]
        if shutil.which('tmux'):
            cases.append(('1', signal.SIGTERM))
        pid_file = self.root / 'child-pid'
        for live, number in cases:
            with self.subTest(live=live, signal=number):
                pid_file.unlink(missing_ok=True)
                with subprocess.Popen(
                    [str(self.bundle / 'tools/run-codex.sh'), str(self.root), '--task', 'cancel'],
                    env=dict(self.env, PATH=f'{self.root}:{self.env["PATH"]}',
                             CODEX_STARTER_KIT_NOTIFY_METADATA='0',
                             CODEX_STARTER_KIT_LIVE_INSPECT=live),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ) as runner:
                    try:
                        deadline = time.monotonic() + 4
                        while not pid_file.exists() or not pid_file.read_text().strip():
                            if time.monotonic() >= deadline or runner.poll() is not None:
                                self.fail('Codex descendant did not start')
                            time.sleep(0.02)
                        pid = int(pid_file.read_text())
                        runner.send_signal(number)
                        out, err = runner.communicate(timeout=6)
                        self.assertEqual(runner.returncode, 128 + number, err)
                        self.assertEqual(err.count('START Codex'), 1)
                        self.assertEqual(out.count('ERROR Codex'), 1)
                        self.assertEqual(out.count(f'SANDBOX_HANDOFF_EXIT_CODE={128 + number}'), 1)
                        self.assertEqual(self.state(), {})
                        registry = self.registry_module()
                        deadline = time.monotonic() + 2
                        while registry.identity(pid) is not None and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertIsNone(registry.identity(pid))
                        if live == '1':
                            socket = re.search(r'tmux -S (\S+) attach -r', out)[1]
                            self.assertFalse(Path(socket).parent.exists())
                    finally:
                        if runner.poll() is None:
                            runner.terminate()
                            try:
                                runner.communicate(timeout=6)
                            except subprocess.TimeoutExpired:
                                runner.kill()
                                runner.communicate(timeout=3)

    def test_session_model_search_has_entry_and_byte_limits(self):
        spec = importlib.util.spec_from_file_location('fixture_notice', SOURCE / 'tools/codex_notice.py')
        notice = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notice)
        session = '01234567-89ab-4def-8123-0123456789ab'
        home = self.root / 'model-home'
        date = home / 'sessions/2026/11/17'
        date.mkdir(parents=True)
        rollout = date / f'rollout-test-{session}.jsonl'
        header = json.dumps({'type': 'session_meta', 'payload': {'id': session}}) + '\n'
        turn = json.dumps({'type': 'turn_context', 'payload': {'model': 'test-model'}}) + '\n'
        rollout.write_text(header + '\n' * 512 + turn)
        with patch.dict(os.environ, CODEX_HOME=str(home)):
            self.assertEqual(notice.session_model(session), 'test-model')
            with patch.object(notice, 'MODEL_MAX_BYTES', len(header) + 256):
                self.assertIsNone(notice.session_model(session))
            with patch.object(notice, 'MODEL_MAX_ENTRIES', 3):
                self.assertIsNone(notice.session_model(session))

    def test_runner_broadcasts_metadata_by_default(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        os.set_blocking(master, False)
        entry = Path(os.ttyname(slave)).name
        registry_script = self.bundle / 'tools/task_registry.py'
        registry_script.write_text(registry_script.read_text().replace('entries = []', f'entries = [{entry!r}]'))
        session = '01234567-89ab-4def-8123-0123456789ab'
        home = self.root / 'codex-home'
        date = home / 'sessions/2026/11/17'
        date.mkdir(parents=True)
        rollout = date / f'rollout-test-{session}.jsonl'
        rollout.write_text(
            json.dumps({'type': 'session_meta', 'payload': {'id': session}}) + '\n'
            + json.dumps({'type': 'turn_context', 'payload': {'model': 'test-model'}}) + '\n')
        codex = self.root / 'codex'
        self.env.update(PATH=f'{self.root}:{self.env["PATH"]}', CODEX_HOME=str(home))
        task = 'first line ' + 'x' * 130 + '\nPRIVATE FULL PROMPT'
        self.write_quota_session('recent.jsonl', [self.quota_event(
            datetime.now(timezone.utc) - timedelta(seconds=5))])
        for setting, code in itertools.product(('0', '1'), (0, 7, 143, 8)):
            with self.subTest(setting=setting, code=code):
                events = [
                    {'type': 'thread.started', 'thread_id': session if code != 8 else task},
                    {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': task}},
                    {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Agent output'}},
                ]
                if code in (0, 7):
                    events.append({'type': 'turn.completed', 'usage': {
                        'input_tokens': 1000, 'cached_input_tokens': 400, 'output_tokens': 25}})
                elif code == 8:
                    events.append({'type': 'turn.completed', 'usage': {
                        'input_tokens': True, 'output_tokens': task}})
                codex.write_text(
                    '#!/bin/bash\n'
                    'if [[ $2 == --help ]]; then echo "  --json"; exit; fi\n'
                    '[[ $* == *--json* ]] || exit 99\n'
                    'cat >/dev/null\n'
                    "cat <<'EVENTS'\nmalformed\n"
                    + '\n'.join(map(json.dumps, events)) + '\nEVENTS\n'
                    + ('kill -TERM "$PPID"\n' if code == 143 else f'exit {code}\n'))
                codex.chmod(0o755)
                env = dict(self.env, CODEX_STARTER_KIT_SHOW_FULL_PROMPT=setting, CODEX_HOME=home.name)
                if setting == '1':
                    env['CODEX_STARTER_KIT_LIMIT_REMINDER'] = '1'
                result = subprocess.run(
                    [str(self.bundle / 'tools/run-codex.sh'), str(self.root), '--task', task],
                    env=env,
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn('START Codex', result.stderr)
                self.assertNotIn('session:', result.stderr)
                task_id = re.search(r' \| task: ([0-9a-f]{12})\n\Z', result.stderr)
                self.assertIsNotNone(task_id, result.stderr)
                self.assertNotIn('PRIVATE FULL PROMPT', result.stdout + result.stderr)
                self.assertTrue(result.stdout.startswith('Agent output\n'))
                start = f'\r\n\x1b[1;36m{result.stderr.rstrip(chr(10))}\x1b[0m\r\n'
                if setting == '1':
                    start += task + '\r\n'
                marker = '❌ ERROR' if code else '✅ END'
                color = 31 if code else 32
                suffix = ''
                if code != 8:
                    tokens = ' | tokens: 1,025' if code in (0, 7) else ''
                    suffix = f' | model: test-model{tokens} | session: {session}'
                notice = (rf'\[\d{{2}}:\d{{2}}:\d{{2}}\] 🤖 0 {marker} Codex '
                          rf'\[{re.escape(self.root.name)}\] duration: \d+s \| task: {task_id[1]}'
                          rf'{re.escape(suffix)}')
                reminder = []
                if setting == '1':
                    reminder = [r'Usage snapshot: \d+s ago',
                                r'⏱️ Codex 5-hour: 75% remaining · resets in (?:1h|\d+m)',
                                r'📅 Codex weekly: 50% remaining · resets in (?:2d|1d 23h \d+m)']
                self.assertRegex(
                    os.read(master, 4096).decode(),
                    rf'^{re.escape(start)}\r\n\a\x1b\[1;{color}m{notice}\x1b\[0m\r\n'
                    + ''.join(line + r'\r\n' for line in reminder) + r'\Z')
                self.assertRegex(result.stdout,
                                 r'^Agent output\n'
                                 + (f'SANDBOX_HANDOFF_SESSION_ID={session}\n' if code != 8 else '')
                                 + rf'{notice}\n'
                                 + ''.join(line + r'\n' for line in reminder)
                                 + rf'SANDBOX_HANDOFF_EXIT_CODE={code}\n\Z')
                self.assertEqual(self.state(), {})

    def quota_event(self, stamp, used=25):
        return {
            'type': 'event_msg', 'timestamp': stamp.isoformat().replace('+00:00', 'Z'),
            'payload': {'type': 'token_count', 'rate_limits': {
                'primary': {'used_percent': used, 'window_minutes': 300,
                            'resets_at': stamp.timestamp() + 3600},
                'secondary': {'used_percent': 50, 'window_minutes': 10080,
                              'resets_at': stamp.timestamp() + 172800},
            }},
        }

    def write_quota_session(self, name, events):
        home = Path(self.env.get('CODEX_HOME', self.root / '.codex'))
        path = home / 'sessions' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('malformed JSON\n' + '\n'.join(map(json.dumps, events)) + '\n')
        return path

    def test_quota_reminder_requires_literal_opt_in_and_valid_snapshot(self):
        now = datetime.now(timezone.utc)
        expired = self.quota_event(now - timedelta(seconds=1))
        expired['payload']['rate_limits']['primary']['resets_at'] = now.timestamp() - 2
        bad_reset = self.quota_event(now - timedelta(seconds=2))
        bad_reset['payload']['rate_limits']['secondary']['resets_at'] *= 1000
        self.write_quota_session('nested/older.jsonl', [
            self.quota_event(now - timedelta(minutes=1), 80),
            self.quota_event(now - timedelta(hours=6)),
        ])
        self.write_quota_session('newer.jsonl', [
            self.quota_event(now - timedelta(seconds=10), 12.5),
            self.quota_event(now + timedelta(hours=1)), expired, bad_reset,
            self.quota_event(now - timedelta(seconds=3), True),
            self.quota_event(now - timedelta(seconds=4), float('nan')),
        ])
        outside = self.root / 'outside.jsonl'
        outside.write_text(json.dumps(self.quota_event(now - timedelta(seconds=1), 99)))
        sessions = self.root / '.codex/sessions'
        (sessions / 'link.jsonl').symlink_to(outside)
        os.mkfifo(sessions / 'pipe.jsonl')

        def finish_codex():
            script = str(self.bundle / 'tools/notify-task-done.sh')
            start = subprocess.run([script, 'start', 'p', 'quota check', str(os.getpid())],
                                   env=self.env, text=True, capture_output=True, timeout=5)
            self.assertEqual(start.returncode, 0, start.stderr)
            self.assertRegex(start.stderr, r'START Codex \[p\] quota check \| task: [0-9a-f]{12}\n\Z')
            result = subprocess.run([script, 'end', start.stdout.strip(), '0'],
                                    env=self.env, text=True, capture_output=True, timeout=5)
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            self.assertEqual(self.state(), {})
            return result.stdout.splitlines()

        for setting in (None, '', '0', 'true', '01', '1'):
            with self.subTest(setting=setting):
                if setting is not None:
                    self.env['CODEX_STARTER_KIT_LIMIT_REMINDER'] = setting
                lines = finish_codex()
                self.assertRegex(lines[0], r'END Codex \[p\] duration: \d+s \| task: [0-9a-f]{12}$')
                self.assertEqual(len(lines), 4 if setting == '1' else 1)
                if setting == '1':
                    self.assertRegex(lines[1], r'Usage snapshot: \d+s ago')
                    self.assertIn('87.5% remaining', lines[2])

        manual = self.run_task('end', self.start().stdout.strip(), '0')
        self.assertRegex(manual.stdout, r'END ChatGPT \[project\] \(\d+s\)\n\Z')
        sessions.rename(sessions.with_name('old-sessions'))
        self.assertIn('87.5% remaining', finish_codex()[2])
        self.env['CODEX_HOME'] = str(self.root / 'other-codex-home')
        self.assertEqual(finish_codex()[1:], ['Codex usage: unavailable'])

    def quota_module(self):
        spec = importlib.util.spec_from_file_location('codex_notice', SOURCE / 'tools/codex_notice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_quota_snapshot_ages_and_expires_across_cache_reads(self):
        module = self.quota_module()
        stamp = datetime(2026, 11, 21, tzinfo=timezone.utc)
        epoch = stamp.timestamp()
        path = self.write_quota_session('2026/11/21/rollout.jsonl', [self.quota_event(stamp)])
        cache = self.root / 'quota.json'
        home = self.root / '.codex'

        def reminder(offset):
            with patch.object(module.time, 'time', return_value=epoch + offset):
                return module.quota_reminder(home, cache)

        fresh = reminder(120)
        self.assertEqual(fresh[0], 'Usage snapshot: 2m ago')
        self.assertIn('75% remaining · resets in 58m', fresh[1])
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        path.unlink()
        self.write_quota_session('2026/11/21/older.jsonl', [
            self.quota_event(stamp - timedelta(seconds=30), 90)])
        self.assertEqual(reminder(120), fresh)
        with patch.object(module.time, 'monotonic', side_effect=itertools.count(step=2)):
            self.assertEqual(reminder(120), fresh)
        self.assertEqual(reminder(900), ('Usage snapshot: 15m ago (stale)',))
        self.assertEqual(reminder(3600), ('Usage snapshot: 1h ago (expired)',))
        self.assertEqual(reminder(-31), ('Codex usage: unavailable',))
        (path.parent / 'older.jsonl').unlink()
        cache.write_text('{' + 'x' * 4096)
        self.assertEqual(reminder(120), ('Codex usage: unavailable',))

    def test_concurrent_codex_ends_share_quota_cache(self):
        self.env['CODEX_STARTER_KIT_LIMIT_REMINDER'] = '1'
        self.write_quota_session('rollout.jsonl', [
            self.quota_event(datetime.now(timezone.utc) - timedelta(seconds=5))])
        script = str(self.bundle / 'tools/notify-task-done.sh')
        starts = [subprocess.run([script, 'start', 'p', 'quota check', str(os.getpid())],
                                 env=self.env, text=True, capture_output=True, timeout=5)
                  for _ in range(4)]
        self.assertTrue(all(result.returncode == 0 for result in starts))

        def finish(start):
            return subprocess.run([script, 'end', start.stdout.strip(), '0'],
                                  env=self.env, text=True, capture_output=True, timeout=5)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(finish, starts))
        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertTrue(all('75% remaining' in result.stdout for result in results))
        self.assertEqual(self.state(), {})
        self.assertEqual(json.loads((self.registry / 'quota.json').read_text())['windows'][0][0], 25)
        self.assertEqual(list(self.registry.glob('.quota-*')), [])

    def test_quota_lookup_reads_complete_records_in_bounded_tails(self):
        module = self.quota_module()
        now = datetime.now(timezone.utc)
        path = self.write_quota_session('2026/11/21/rollout.jsonl', [
            self.quota_event(now - timedelta(seconds=30), 90)])
        with path.open('a') as stream:
            stream.write('x' * (256 * 1024) + '\n')
            stream.write(json.dumps(self.quota_event(now - timedelta(seconds=10), 25)) + '\n')
            stream.write(json.dumps(self.quota_event(now - timedelta(seconds=5), 50)))
        cache = self.root / 'quota.json'
        target = self.root / 'outside'
        target.write_text('untouched')
        cache.symlink_to(target)
        reminder = module.quota_reminder(self.root / '.codex', cache)
        self.assertIn('75% remaining', reminder[1])
        self.assertEqual(target.read_text(), 'untouched')
        self.assertFalse(cache.is_symlink())

    def test_runner_metadata_without_json_support(self):
        codex = self.root / 'codex'
        codex.write_text('#!/bin/bash\n'
                         'if [[ $2 == --help ]]; then echo "Usage: codex exec"; exit; fi\n'
                         '[[ $* != *--json* ]] || exit 99\n'
                         'cat\nexit 6\n')
        codex.chmod(0o755)
        self.env['PATH'] = f'{self.root}:{self.env["PATH"]}'
        result = subprocess.run(
            [str(self.bundle / 'tools/run-codex.sh'), str(self.root), '--task', 'fallback'],
            env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertTrue(result.stdout.startswith('fallback\n'))
        self.assertRegex(result.stdout, r'ERROR Codex .* duration: \d+s \| task: [0-9a-f]{12}\n'
                         r'SANDBOX_HANDOFF_EXIT_CODE=6\n$')
        self.assertEqual(self.state(), {})

    def test_terminal_payload_owner_and_bell(self):
        spec = importlib.util.spec_from_file_location('notify', SOURCE / 'tools/task_registry.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        entry = Path(os.ttyname(slave)).name
        output = io.StringIO()
        with patch.object(module.os, 'listdir', return_value=[entry]):
            module.deliver('🤖 0 ✅ END Codex [p] duration: 2s', 'END', 0, output)
        self.assertNotIn('\x1b', output.getvalue())
        payload = os.read(master, 4096)
        self.assertIn(b'\a', payload)
        self.assertIn(b'\x1b[1;32m', payload)
        with patch.object(module.os, 'listdir', return_value=[entry]), patch.object(module.os, 'getuid', return_value=-1), patch.object(module.os, 'open') as opened:
            module.deliver('notice', 'START', 0, output)
            opened.assert_not_called()
        real_open = os.open
        with patch.object(module.os, 'listdir', return_value=[entry]), patch.object(module.os, 'open', wraps=real_open) as opened:
            module.deliver('start notice', 'START', 0, output)
            flags = opened.call_args.args[1]
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertTrue(flags & os.O_NONBLOCK)
        payload = os.read(master, 4096)
        self.assertNotIn(b'\a', payload)
        self.assertIn(b'\x1b[1;36m', payload)
        with patch.object(module.os, 'listdir', return_value=[entry]), patch.object(module.os, 'write') as write:
            module.deliver('start notice', 'START', 0, output, 'raw\n\\prompt')
            write.assert_called_once()
            self.assertEqual(write.call_args.args[1], b'\r\n\x1b[1;36mstart notice\x1b[0m\r\nraw\n\\prompt\r\n')
        # Recheck ownership after open, guarding a path change between stat/open.
        wrong_owner = os.stat_result((os.fstat(slave).st_mode, 0, 0, 1, -1, 0, 0, 0, 0, 0))
        with patch.object(module.os, 'listdir', return_value=[entry]), patch.object(module.os, 'fstat', return_value=wrong_owner), patch.object(module.os, 'write') as write:
            module.deliver('end notice', 'END', 1, output)
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
