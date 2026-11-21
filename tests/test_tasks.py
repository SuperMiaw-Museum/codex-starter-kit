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
import shutil
import subprocess
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
        path = self.bundle / 'tools/task-registry.py'
        code = path.read_text().replace("Path('/tmp') / f'codex-starter-kit-tasks-{os.getuid()}'", repr(str(self.registry)))
        code = code.replace('ROOT = ', 'ROOT = Path(' , 1).replace('\nTTL =', ')\nTTL =', 1)
        # Isolated subprocesses never send notices to real user terminals.
        code = code.replace("entries = os.listdir('/dev/pts')", 'entries = []')
        path.write_text(code)
        self.env = dict(os.environ, HOME=str(self.root), NVM_DIR=str(self.root / 'nvm'))
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

    def test_manual_full_prompt_format(self):
        self.env['CODEX_STARTER_KIT_SHOW_FULL_PROMPT'] = '1'
        label = 'Short manual task\nwith details'
        result = self.start(label)
        self.assertRegex(result.stderr, r'🚀 START Manual \[project\]\n$')
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
        self.assertNotIn('SANDBOX_HANDOFF_TMUX_SESSION', result.stdout)
        self.assertIn('a task\nsecond line\n', result.stdout)
        self.assertIn('ARGS:exec --skip-git-repo-check -', result.stdout)
        self.assertEqual(len(self.state()), 1)

    def test_live_inspection_requires_tmux(self):
        for name in ('bash', 'basename', 'dirname'):
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
        registry_script = self.bundle / 'tools/task-registry.py'
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

    def test_runner_broadcasts_metadata_by_default(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        os.set_blocking(master, False)
        entry = Path(os.ttyname(slave)).name
        registry_script = self.bundle / 'tools/task-registry.py'
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
                env = dict(self.env, CODEX_STARTER_KIT_SHOW_FULL_PROMPT=setting)
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
                    reminder = [r'⏱️ Codex 5-hour: 75% remaining · resets in (?:1h|\d+m)',
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
        path = self.root / '.codex/sessions' / name
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
                self.assertEqual(len(lines), 3 if setting == '1' else 1)
                if setting == '1':
                    self.assertIn('87.5% remaining', lines[1])

        manual = self.run_task('end', self.start().stdout.strip(), '0')
        self.assertRegex(manual.stdout, r'END Manual \[project\] \(\d+s\)\n\Z')
        sessions.rename(sessions.with_name('old-sessions'))
        self.assertEqual(len(finish_codex()), 1)

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
        spec = importlib.util.spec_from_file_location('notify', SOURCE / 'tools/task-registry.py')
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
