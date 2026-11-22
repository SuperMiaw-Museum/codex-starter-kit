"""Offline bootstrap tests; never touch the real home or systemd manager."""
import fcntl
import os
from pathlib import Path
import pty
import shutil
import shlex
import signal
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'bootstrap'


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bootstrap test ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / 'checkout with spaces' / 'bootstrap'
        shutil.copytree(SOURCE, self.bundle)
        self.version = (self.bundle / 'version.txt').read_text().strip()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'systemctl-calls'
        self.stub('systemctl', '[[ $* == "--user show-environment" ]] && exit 0; printf "%s\\n" "$*" >> "$CALLS"')
        for name in ('tmux', 'node', 'npx'):
            self.stub(name, 'exit 0')
        self.env = dict(os.environ, HOME=str(self.home),
                        NVM_DIR=str(self.home / '.nvm'),
                        BUN_INSTALL=str(self.home / '.bun'),
                        XDG_STATE_HOME=str(self.home / '.local/state'),
                        XDG_CONFIG_HOME=str(self.home / '.config'),
                        PATH=str(self.bin) + ':' + os.environ['PATH'], CALLS=str(self.calls))
        registry = self.bundle / 'internal/task_registry.py'
        registry.write_text(registry.read_text().replace(
            "Path('/tmp') / f'codex-starter-kit-tasks-{os.getuid()}'",
            f'Path({str(self.root / "task-registry")!r})'))
        self.state = self.root / 'state'
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.process(10, '/usr/bin/bash', ['bash'], 1)
        helper = self.bundle / 'internal/agent_ancestry.py'
        helper.write_text(helper.read_text().replace("Path('/proc')", f'Path({str(self.proc)!r})'))
        reminder = self.bundle / 'internal/remote_control.py'
        reminder.write_text(reminder.read_text().replace('os.getppid()', '10'))

    def process(self, pid, executable, argv, parent, uid=1000):
        base = self.proc / str(pid)
        base.mkdir(exist_ok=True)
        exe = base / 'exe'
        exe.unlink(missing_ok=True)
        exe.symlink_to(executable)
        (base / 'cmdline').write_bytes(('\0'.join(argv) + '\0').encode())
        (base / 'comm').write_text(Path(executable).name + '\n')
        (base / 'status').write_text(
            f'Name:\t{Path(executable).name}\nPPid:\t{parent}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o755)

    def run_script(self, name, *args):
        return subprocess.run(['/bin/bash', str(self.bundle / name), *args],
                              env=self.env, capture_output=True, text=True)

    def automatic(self):
        master, slave = pty.openpty()
        process = subprocess.Popen(['/bin/bash', str(self.bundle / 'remote-control.sh'), '--auto'],
                                   env=self.env, stdin=slave, stderr=slave, stdout=subprocess.PIPE)
        os.close(slave)
        return process, master

    def finish(self, pair):
        process, master = pair
        process.communicate(timeout=10)
        output = b''
        try:
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
        finally:
            os.close(master)
        self.assertEqual(process.returncode, 0)
        return output

    def test_show_absolute_prompt_without_state(self):
        result = self.run_script('remote-control.sh', '--show')
        self.assertEqual(result.returncode, 0)
        self.assertIn(f'Read "{self.bundle}/PROMPT.md"', result.stderr)
        self.assertIn(f'CODEX STARTER KIT · {self.version}', result.stderr)
        self.assertNotIn('\x1b[', result.stderr)
        self.assertFalse(self.state.exists())

    def test_nonterminal_silent(self):
        result = self.run_script('remote-control.sh', '--auto')
        self.assertEqual(result.stderr, '')
        self.assertFalse(self.state.exists())

    def test_every_eligible_session_displays_reminder(self):
        self.assertIn('COPY THIS PROMPT INTO CHATGPT',
                      self.run_script('remote-control.sh', '--show').stderr)
        sessions = [self.automatic() for _ in range(5)]
        outputs = [self.finish(pair) for pair in sessions]
        self.assertEqual(sum(b'COPY THIS PROMPT INTO CHATGPT' in out for out in outputs), 5)
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))

    def test_bridge_helper_loads_nvm_and_signals_exit(self):
        nvm = self.home / '.nvm'
        nvm.mkdir()
        (nvm / 'nvm.sh').write_text('export TEST_NVM_LOADED=yes\n')
        self.env['NVM_DIR'] = str(nvm)
        self.stub('npx', '[[ $TEST_NVM_LOADED == yes ]] || exit 99; exit 23')
        self.stub('tmux', 'printf "%s\n" "$*" >> "$CALLS"')
        result = self.run_script('internal/desktop-commander.sh', '--inside-tmux')
        self.assertEqual(result.returncode, 23)
        self.assertEqual(self.calls.read_text(), 'wait-for -S desktop-commander-exit\n')

    @unittest.skipUnless(shutil.which('tmux'), 'requires local tmux')
    def test_detached_bridge_signals_existing_server_with_stale_path(self):
        executable = shutil.which('tmux')
        socket = self.root / 'test.sock'
        client = [executable, '-S', str(socket)]
        server_env = dict(self.env, PATH='/nonexistent', SHELL='/bin/sh')
        server_env.pop('TMUX', None)
        subprocess.run([*client, '-f', '/dev/null', 'new-session', '-d', '-s', 'fixture',
                        '/bin/sleep', '30'], env=server_env, check=True, timeout=5)
        self.addCleanup(subprocess.run, [*client, 'kill-server'],
                        capture_output=True, timeout=5)
        self.stub('tmux', f'exec {shlex.join(client)} "$@"')
        reached = self.root / 'fake-npx-ran'
        self.stub('npx', f'printf ran > {shlex.quote(str(reached))}; exit 23')
        result = subprocess.run(['/bin/bash', str(self.bundle / 'internal/desktop-commander.sh')],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(reached.read_text(), 'ran')
        server_path = subprocess.check_output([*client, 'show-environment', '-g', 'PATH'],
                                              text=True, timeout=5)
        self.assertEqual(server_path.strip(), 'PATH=/nonexistent')

    def test_bridge_pane_forwards_termination_and_signals_waiter(self):
        ready = self.home / 'bridge-ready'
        self.stub('npx', 'touch "$HOME/bridge-ready"; exec /bin/sleep 20')
        self.stub('tmux', 'printf "%s\n" "$*" >> "$CALLS"')
        with subprocess.Popen(
            ['/bin/bash', str(self.bundle / 'internal/desktop-commander.sh'), '--inside-tmux'],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        ) as pane:
            try:
                deadline = time.monotonic() + 5
                while not ready.exists():
                    self.assertIsNone(pane.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                pane.terminate()
                pane.communicate(timeout=5)
                self.assertEqual(pane.returncode, 143)
                self.assertEqual(self.calls.read_text(), 'wait-for -S desktop-commander-exit\n')
            finally:
                if pane.poll() is None:
                    os.killpg(pane.pid, signal.SIGKILL)
                    pane.communicate(timeout=5)

    def test_one_shot_prints_reminder_before_foreground_bridge(self):
        nvm = self.home / '.nvm'
        nvm.mkdir()
        (nvm / 'nvm.sh').write_text('export TEST_NVM_LOADED=yes\n')
        self.env['NVM_DIR'] = str(nvm)
        self.stub('systemctl', 'printf "inactive\\n"; exit 3')
        self.stub('tmux', '[[ $* == "has-session -t =desktop-commander" ]] || touch "$CALLS"; exit 1')
        self.stub('npx', '[[ $TEST_NVM_LOADED == yes ]] || exit 99\n'
                         'printf "bridge: %s\\n" "$*" >&2\nexit 23')
        result = self.run_script('remote-control.sh', '--once')
        self.assertEqual(result.returncode, 23)
        self.assertIn(f'Read "{self.bundle}/PROMPT.md"', result.stderr)
        self.assertIn(f'CODEX STARTER KIT · {self.version}', result.stderr)
        self.assertLess(result.stderr.index('COPY THIS PROMPT INTO CHATGPT'),
                        result.stderr.index('bridge: @wonderwhy-er/desktop-commander@latest remote'))
        self.assertEqual(list(self.home.iterdir()), [nvm])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.calls.exists())

    def test_one_shot_rejects_running_bridge(self):
        self.stub('npx', 'touch "$CALLS"; exit 99')
        for status in ('active', 'activating', 'deactivating'):
            with self.subTest(status=status):
                self.stub('systemctl', f'echo {status}; exit 3')
                result = self.run_script('remote-control.sh', '--once')
                self.assertEqual(result.returncode, 1)
                self.assertIn(f'desktop-commander.service is {status}', result.stderr)
        self.stub('systemctl', 'exit 1')
        result = self.run_script('remote-control.sh', '--once')
        self.assertEqual(result.returncode, 1)
        self.assertIn('tmux session already exists', result.stderr)
        self.assertFalse(self.calls.exists())

    def test_one_shot_rejects_locked_checkout_without_creating_state(self):
        self.stub('npx', 'touch "$CALLS"; exit 99')
        directory = os.open(self.bundle, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(directory, fcntl.LOCK_EX)
            result = self.run_script('remote-control.sh', '--once')
        finally:
            os.close(directory)
        self.assertEqual(result.returncode, 1)
        self.assertIn('one-shot bridge may already be starting or running', result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.state.exists())

    def test_foreground_holds_lock_through_exec_and_releases_on_interrupt(self):
        self.stub('systemctl', 'exit 3')
        self.stub('tmux', 'exit 1')
        self.stub('npx', 'echo "$$" > "$CALLS"; exec /bin/sleep 20')
        with subprocess.Popen(
            ['/bin/bash', str(self.bundle / 'remote-control.sh'), '--once'],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        ) as bridge:
            try:
                deadline = time.monotonic() + 5
                while not self.calls.exists() or not self.calls.read_text().strip():
                    self.assertIsNone(bridge.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                self.assertEqual(int(self.calls.read_text()), bridge.pid)
                blocked = self.run_script('remote-control.sh', '--once')
                self.assertEqual(blocked.returncode, 1)
                self.assertIn('Could not lock this checkout', blocked.stderr)
                bridge.send_signal(signal.SIGINT)
                bridge.communicate(timeout=5)
                self.assertEqual(bridge.returncode, -signal.SIGINT)
            finally:
                if bridge.poll() is None:
                    bridge.kill()
                    bridge.communicate(timeout=5)
        self.stub('npx', 'exit 23')
        self.assertEqual(self.run_script('remote-control.sh', '--once').returncode, 23)

    @unittest.skipUnless(shutil.which('tmux'), 'tmux is required for the service integration test')
    def test_detached_bridge_wakes_service_waiter_on_isolated_tmux(self):
        tmux = shutil.which('tmux')
        socket = self.root / 'tmux.sock'
        self.env.update(TEST_TMUX=tmux, TEST_SOCKET=str(socket))
        self.stub('tmux', 'exec "$TEST_TMUX" -S "$TEST_SOCKET" -f /dev/null "$@"')
        self.stub('npx', 'printf "%s\n" "$*" > "$CALLS"; exit 23')
        self.addCleanup(subprocess.run, [tmux, '-S', str(socket), 'kill-server'],
                        capture_output=True, timeout=5)
        result = subprocess.run(
            ['/bin/bash', str(self.bundle / 'internal/desktop-commander.sh')],
            env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.calls.read_text(), '@wonderwhy-er/desktop-commander@latest remote\n')

    def test_invalid_remote_usage_and_standalone_ancestry(self):
        for args in (('--once', 'extra'), ('--unknown',)):
            result = self.run_script('remote-control.sh', *args)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertIn('Usage:', result.stderr)
        self.assertEqual(self.run_script('internal/agent-ancestry.sh', '10').returncode, 0)
        self.process(10, '/usr/bin/bash', ['bash'], 20)
        self.process(20, '/usr/bin/bash', ['bash'], 10)
        for pid in ('10', '01', '-1', 'bad'):
            result = self.run_script('internal/agent-ancestry.sh', pid)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, '', ''))

    def test_agent_ancestry(self):
        for executable, argv in (
            ('/usr/bin/codex', ['codex', 'exec']),
            ('/usr/bin/node', ['node', '/x/@openai/codex/bin/codex.js']),
            ('/usr/bin/node', ['node', '/x/@wonderwhy-er/desktop-commander/dist/index.js', 'remote']),
            ('/usr/bin/bash', ['bash', '/x/run-codex.sh']),
            ('/usr/bin/remote_desktop_commander', ['remote_desktop_commander']),
            ('/usr/bin/python3.13', ['python3', '-B', '/x/bootstrap/internal/run_codex.py']),
            ('/usr/bin/python3.13', ['python3', '-B', '/x/bootstrap/internal/bridge.py']),
            ('/usr/bin/python3.13', ['python3', '-B', '/x/bootstrap/tools/run_codex.py']),
            ('/usr/bin/python3.13', ['python3', '-B', '/x/bootstrap/tools/bridge.py']),
        ):
            self.process(10, '/usr/bin/bash', ['bash'], 20)
            self.process(20, executable, argv, 1)
            self.assertEqual(self.finish(self.automatic()), b'')
            self.assertFalse(self.state.exists())

    def test_ssh_username_and_arbitrary_arguments(self):
        self.process(10, '/usr/sbin/sshd-session', ['sshd-session: codex@pts/1'], 20)
        self.process(20, '/usr/sbin/sshd-session', ['sshd-session'], 30, uid=0)
        self.process(30, '/usr/sbin/sshd', ['sshd'], 1, uid=0)
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))
        self.process(10, '/usr/bin/bash', ['bash', '-c', 'echo codex desktop-commander'], 1)
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))

    def test_real_ssh_chain_with_all_ssh_executables_unreadable(self):
        self.process(10, '/usr/bin/bash', ['-bash'], 20)
        self.process(20, '/usr/lib/openssh/sshd-session',
                     ['sshd-session: codex@pts/0'], 30)
        self.process(30, '/usr/lib/openssh/sshd-session',
                     ['sshd-session: codex [priv]'], 40, uid=0)
        self.process(40, '/usr/sbin/sshd', ['sshd: /usr/sbin/sshd -D [listener]'], 1, uid=0)
        for pid in (20, 30, 40):
            (self.proc / str(pid) / 'exe').unlink()
            (self.proc / str(pid) / 'cmdline').unlink()
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))
        # An agent below the SSH boundary must still suppress the reminder.
        self.process(10, '/usr/bin/bash', ['bash'], 15)
        self.process(15, '/usr/bin/node', ['node', '/x/@openai/codex/bin/codex.js'], 20)
        self.assertEqual(self.finish(self.automatic()), b'')

    def test_incomplete_ssh_chain_fails_closed(self):
        self.process(10, '/usr/bin/bash', ['bash'], 20)
        self.process(20, '/usr/sbin/sshd-session', ['sshd-session: codex@pts/0'], 30)
        (self.proc / '20/exe').unlink()
        self.assertEqual(self.finish(self.automatic()), b'')

    def test_unknown_inaccessible_non_ssh_and_mismatched_ssh_metadata(self):
        self.process(10, '/usr/bin/bash', ['bash'], 20)
        self.process(20, '/usr/bin/unknown', ['unknown', 'sshd-session: codex'], 1)
        (self.proc / '20/exe').unlink()
        self.assertEqual(self.finish(self.automatic()), b'')
        self.process(20, '/usr/sbin/sshd', ['sshd'], 1, uid=0)
        (self.proc / '20/comm').write_text('unknown\n')
        self.assertEqual(self.finish(self.automatic()), b'')
        self.assertFalse(self.state.exists())

    def test_manual_and_auto_tty_color(self):
        self.assertIn(b'\x1b[1;36m', self.finish(self.automatic()))
        master, slave = pty.openpty()
        process = subprocess.Popen(['/bin/bash', str(self.bundle / 'remote-control.sh'), '--show'],
                                   env=self.env, stderr=slave, stdout=subprocess.PIPE)
        os.close(slave)
        output = self.finish((process, master))
        self.assertIn(b'\x1b[1;36m', output)
        prompt = f'Read "{self.bundle}/PROMPT.md" via Remote Desktop Commander, treat it as operational guidance for this session, acknowledge, and wait for my next request.'
        self.assertIn(prompt.encode(), output)

    def test_preflight_profiles_and_missing_dependencies(self):
        # A restricted PATH exposes missing commands without relying on host packages.
        isolated = self.root / 'isolated'
        isolated.mkdir()
        names = ('bash', 'python3', 'dirname')
        for name in names:
            (isolated / name).symlink_to(shutil.which(name))
        self.env['PATH'] = str(isolated)
        result = self.run_script('check-requirements.sh', 'hook')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('SKIP', result.stdout)
        self.assertNotIn('\x1b[', result.stdout)
        result = self.run_script('check-requirements.sh', 'tools')
        self.assertEqual(result.returncode, 1)
        self.assertIn('KO', result.stdout)
        self.assertIn('codex:', result.stdout)
        (isolated / 'bash').unlink()
        rc = self.home / '.bashrc'
        rc.write_text('original\n')
        result = self.run_script('setup.sh', '--install-hook')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(rc.read_text(), 'original\n')
        self.assertEqual(list(self.home.iterdir()), [rc])
        self.assertEqual(self.run_script('check-requirements.sh', 'invalid').returncode, 2)
        result = self.run_script('setup.sh', '--preview')
        self.assertEqual(result.returncode, 1)
        self.assertIn('managed unit v1', result.stdout)
        self.assertEqual(list(self.home.iterdir()), [rc])
        result = self.run_script('setup.sh', '--install-service')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(list(self.home.iterdir()), [rc])
        self.assertFalse(self.calls.exists())

    def test_preflight_tty_color_and_all_status(self):
        master, slave = pty.openpty()
        process = subprocess.Popen(['/bin/bash', str(self.bundle / 'check-requirements.sh'), 'hook'],
                                   env=self.env, stdout=slave, stderr=slave)
        os.close(slave)
        output = self.finish((process, master))
        self.assertIn(b'\x1b[32m', output)
        self.assertIn('✅ OK'.encode(), output)
        self.assertIn(b'Summary:', output)
        self.stub('codex', 'exit 0')
        result = self.run_script('check-requirements.sh', 'all')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('0 KO', result.stdout)
        self.assertNotIn('SKIP', result.stdout)

    def test_missing_python_has_minimal_read_only_report(self):
        isolated = self.root / 'no-python'
        isolated.mkdir()
        for name in ('bash', 'dirname'):
            (isolated / name).symlink_to(shutil.which(name))
        self.env['PATH'] = str(isolated)
        for profile in ('hook', 'service', 'tools', 'all'):
            result = self.run_script('check-requirements.sh', profile)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('python3: missing', result.stdout)
            self.assertIn('Summary: 0 OK, 1 KO', result.stdout)
        auto = self.run_script('remote-control.sh', '--auto')
        self.assertEqual((auto.returncode, auto.stdout, auto.stderr), (0, '', ''))
        setup = self.run_script('setup.sh', '--install-hook')
        self.assertEqual(setup.returncode, 1)
        self.assertIn('Setup requires python3', setup.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse(self.calls.exists())

    def test_service_does_not_require_codex(self):
        isolated = self.root / 'service-only'
        isolated.mkdir()
        for name in ('bash', 'dirname', 'python3'):
            (isolated / name).symlink_to(shutil.which(name))
        for name in ('tmux', 'node', 'npx', 'systemctl'):
            (isolated / name).symlink_to(self.bin / name)
        self.env['PATH'] = str(isolated)
        result = self.run_script('check-requirements.sh', 'service')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('SKIP', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_unreadable_ancestry(self):
        (self.proc / '10/status').unlink()
        self.assertEqual(self.finish(self.automatic()), b'')
        self.assertFalse(self.state.exists())

    def test_reminder_ignores_old_receipts(self):
        receipts = self.home / '.local/state/codex-starter-kit'
        receipts.mkdir(parents=True, mode=0o700)
        receipt = receipts / 'hook'
        receipt.write_text('0.0.1\n')
        (self.bundle / 'version.txt').unlink()
        output = self.finish(self.automatic())
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', output)
        self.assertIn(b'Remote Desktop Commander:', output)
        self.assertNotIn(b'Setup outdated', output)
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))
        self.assertEqual(receipt.read_text(), '0.0.1\n')

    def test_hook_idempotence_and_preservation(self):
        rc = self.home / '.bashrc'
        rc.write_text('# existing\nexport KEEP=yes\n')
        rc.chmod(0o640)
        self.assertEqual(self.run_script('setup.sh', '--install-hook').returncode, 0)
        with rc.open('a') as file:
            file.write('# added after setup\n')
        first = rc.read_bytes()
        inode = rc.stat().st_ino
        (self.bundle / 'version.txt').unlink()
        self.assertEqual(self.run_script('setup.sh', '--install-hook').returncode, 0)
        self.assertEqual(rc.read_bytes(), first)
        self.assertTrue(first.startswith(b'# existing\nexport KEEP=yes\n'))
        self.assertTrue(first.endswith(b'# added after setup\n'))
        self.assertEqual(first.count(b'# BEGIN'), 1)
        self.assertEqual(rc.stat().st_ino, inode)
        self.assertEqual(list(self.home.iterdir()), [rc])
        self.assertFalse(self.calls.exists())
        self.assertEqual(rc.stat().st_mode & 0o777, 0o640)
        subprocess.run(['bash', '-n', str(rc)], check=True)
        rc.write_bytes(first.replace(b'--auto', b'--show'))
        self.assertEqual(self.run_script('setup.sh', '--install-hook').returncode, 0)
        self.assertEqual(rc.read_bytes(), first)

    def test_hook_rejects_malformed_blocks_and_symlinks(self):
        rc = self.home / '.bashrc'
        rc.write_bytes(b'# BEGIN codex-starter-kit reminder\nkeep me\n')
        original = rc.read_bytes()
        result = self.run_script('setup.sh', '--install-hook')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Malformed reminder block', result.stderr)
        self.assertEqual(rc.read_bytes(), original)
        target = self.root / 'target'
        rc.rename(target)
        rc.symlink_to(target)
        result = self.run_script('setup.sh', '--install-hook')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Refusing non-regular .bashrc', result.stderr)
        self.assertTrue(rc.is_symlink())
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.home.iterdir()), [rc])

    def test_service_retries_failed_reload_without_replacing_unit_or_backup(self):
        unit = self.home / '.config/systemd/user/desktop-commander.service'
        unit.parent.mkdir(parents=True)
        unit.write_text('previous unit\n')
        unit.chmod(0o640)
        backup = unit.with_suffix('.service.backup')
        pending = unit.with_name(f'.{unit.name}.reload-pending')
        self.stub('systemctl',
                  '[[ $* == "--user show-environment" ]] && exit 0\n'
                  'printf "%s\\n" "$*" >> "$CALLS"\n'
                  '[[ -e "$HOME/allow-reload" ]] || exit 7')

        failed = self.run_script('setup.sh', '--install-service')
        self.assertEqual(failed.returncode, 7, failed.stderr)
        self.assertIn('Rerun --install-service', failed.stderr)
        self.assertIn('managed unit v1', unit.read_text())
        self.assertTrue(pending.exists())
        self.assertEqual(backup.read_text(), 'previous unit\n')
        self.assertEqual(backup.stat().st_mode & 0o777, 0o640)
        inodes = (unit.stat().st_ino, backup.stat().st_ino)

        (self.home / 'allow-reload').touch()
        preview = self.run_script('setup.sh', '--preview')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertTrue(pending.exists())
        self.assertEqual(self.calls.read_text(), '--user daemon-reload\n')
        retried = self.run_script('setup.sh', '--install-service')
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertIn('daemon reloaded', retried.stdout)
        self.assertFalse(pending.exists())
        self.assertEqual((unit.stat().st_ino, backup.stat().st_ino), inodes)
        current = self.run_script('setup.sh', '--install-service')
        self.assertIn('Service content already current.', current.stdout)
        self.assertEqual(self.calls.read_text(), '--user daemon-reload\n' * 2)

    def test_service_preview_install_update(self):
        unit = self.home / '.config/systemd/user/desktop-commander.service'
        result = self.run_script('setup.sh')
        self.assertIn('managed unit v1', result.stdout)
        self.assertFalse(unit.exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.home / '.local/state/codex-starter-kit').exists())
        (self.bundle / 'version.txt').unlink()
        self.assertEqual(self.run_script('setup.sh', '--install-service').returncode, 0)
        self.assertEqual(self.calls.read_text(), '--user daemon-reload\n')
        self.assertEqual(self.run_script('setup.sh', '--install-service').returncode, 0)
        self.assertEqual(self.calls.read_text(), '--user daemon-reload\n')
        unit.write_text('old installed unit\n')
        self.assertEqual(self.run_script('setup.sh', '--install-service').returncode, 0)
        self.assertEqual(Path(str(unit) + '.backup').read_text(), 'old installed unit\n')
        self.assertEqual(self.calls.read_text(), '--user daemon-reload\n' * 2)
        self.assertEqual(list(self.home.iterdir()), [self.home / '.config'])
        self.assertIn('Restart=always', unit.read_text())
        self.assertIn('checkout with spaces', unit.read_text())


if __name__ == '__main__':
    unittest.main()
