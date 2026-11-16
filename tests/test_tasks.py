"""Offline task tests with private registry, fake Codex, and no terminal broadcast."""
import concurrent.futures
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
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
        path = self.bundle / 'tools/task-registry.py'
        code = path.read_text().replace("Path('/tmp') / f'codex-starter-kit-tasks-{os.getuid()}'", repr(str(self.registry)))
        code = code.replace('ROOT = ', 'ROOT = Path(' , 1).replace('\nTTL =', ')\nTTL =', 1)
        # Isolated subprocesses never send notices to real user terminals.
        code = code.replace("entries = os.listdir('/dev/pts')", 'entries = []')
        path.write_text(code)
        self.env = dict(os.environ, HOME=str(self.root), NVM_DIR=str(self.root / 'nvm'))

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

    def test_format_validation_and_end_failure(self):
        start = self.start('hello\n\x1b[31m' + 'x' * 200)
        self.assertIn('🤖 1 🚀 START Manual [project] — hello ', start.stderr)
        self.assertNotIn('\x1b', start.stderr)
        token = start.stdout.strip()
        self.assertEqual(self.run_task('end', token, '256').returncode, 2)
        self.assertEqual(len(self.state()), 1)
        result = self.run_task('end', token, '7')
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, r'🤖 0 ❌ ERROR Manual \[project\] \(\d+s\)')
        self.assertEqual(self.run_task('end', token, '0').returncode, 3)
        self.assertIn('🤖 0 active tasks', self.run_task('status').stdout)

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
        path = self.bundle / 'tools/task-registry.py'
        path.write_text(path.read_text().replace(
            '    # Broken stdout',
            "    with open('events.jsonl', 'a') as log:\n"
            "        log.write(json.dumps([phase, len(read_state()), message]) + '\\n')\n"
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
        spec = importlib.util.spec_from_file_location('tasks', self.bundle / 'tools/task-registry.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cwd = os.getcwd()
        try:
            with patch.object(module.os, 'replace', side_effect=OSError('write failed')):
                with patch.object(module, 'deliver') as delivered:
                    with self.assertRaises(OSError):
                        module.main(['end', token, '0'])
                    delivered.assert_not_called()
        finally:
            os.chdir(cwd)
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
        self.assertIn('a task\nsecond line\n', result.stdout)
        self.assertIn('ARGS:exec --skip-git-repo-check -', result.stdout)
        self.assertEqual(len(self.state()), 1)

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

    def test_terminal_payload_owner_and_bell(self):
        spec = importlib.util.spec_from_file_location('notify', SOURCE / 'tools/task-registry.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        entry = Path(os.ttyname(slave)).name
        output = io.StringIO()
        with patch.object(module.os, 'listdir', return_value=[entry]):
            module.deliver('🤖 0 ✅ END Codex [p] (2s)', 'END', 0, output)
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
        # Recheck ownership after open, guarding a path change between stat/open.
        wrong_owner = os.stat_result((os.fstat(slave).st_mode, 0, 0, 1, -1, 0, 0, 0, 0, 0))
        with patch.object(module.os, 'listdir', return_value=[entry]), patch.object(module.os, 'fstat', return_value=wrong_owner), patch.object(module.os, 'write') as write:
            module.deliver('end notice', 'END', 1, output)
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
