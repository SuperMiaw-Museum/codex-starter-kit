"""Offline bootstrap tests; never touch the real home or systemd manager."""
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'bootstrap'


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bootstrap test ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / 'checkout with spaces' / 'bootstrap'
        shutil.copytree(SOURCE, self.bundle)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'systemctl-calls'
        self.stub('systemctl', '[[ $* == "--user show-environment" ]] && exit 0; printf "%s\\n" "$*" >> "$CALLS"')
        for name in ('tmux', 'node', 'npx'):
            self.stub(name, 'exit 0')
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_STATE_HOME=str(self.home / '.local/state'),
                        XDG_CONFIG_HOME=str(self.home / '.config'),
                        PATH=str(self.bin) + ':' + os.environ['PATH'], CALLS=str(self.calls))
        # Isolate reminder state; deterministic Linux boot and process fixtures.
        self.state = self.root / 'state'
        script = self.bundle / 'remote-control.sh'
        script.write_text(script.read_text().replace(
            '/tmp/codex-starter-kit-reminder-$UID', '"' + str(self.state) + '"'))
        self.boot = self.root / 'boot-id'
        self.boot.write_text('11111111-1111-1111-1111-111111111111\n')
        script.write_text(script.read_text().replace('/proc/sys/kernel/random/boot_id', '"' + str(self.boot) + '"'))
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.process(10, '/usr/bin/bash', ['bash'], 1)
        helper = self.bundle / 'agent-ancestry.sh'
        helper.write_text(helper.read_text().replace('proc=/proc', f'proc="{self.proc}"'))
        script.write_text(script.read_text().replace('"$PPID"', '10'))

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
        self.assertNotIn('\x1b[', result.stderr)
        self.assertFalse(self.state.exists())

    def test_nonterminal_silent(self):
        result = self.run_script('remote-control.sh', '--auto')
        self.assertEqual(result.stderr, '')
        self.assertFalse(self.state.exists())

    def test_concurrent_once_and_new_boot(self):
        sessions = [self.automatic() for _ in range(5)]
        outputs = [self.finish(pair) for pair in sessions]
        self.assertEqual(sum(b'COPY THIS PROMPT INTO CHATGPT' in out for out in outputs), 5)
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))
        self.boot.write_text('22222222-2222-2222-2222-222222222222\n')
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))

    def test_bridge_helper_loads_nvm_and_signals_exit(self):
        nvm = self.home / '.nvm'
        nvm.mkdir()
        (nvm / 'nvm.sh').write_text('export TEST_NVM_LOADED=yes\n')
        self.env['NVM_DIR'] = str(nvm)
        self.stub('npx', '[[ $TEST_NVM_LOADED == yes ]] || exit 99; exit 23')
        self.stub('tmux', 'printf "%s\n" "$*" >> "$CALLS"')
        result = self.run_script('tools/desktop-commander.sh', '--inside-tmux')
        self.assertEqual(result.returncode, 23)
        self.assertEqual(self.calls.read_text(), 'wait-for -S desktop-commander-exit\n')

    def test_agent_ancestry(self):
        for executable, argv in (
            ('/usr/bin/codex', ['codex', 'exec']),
            ('/usr/bin/node', ['node', '/x/@openai/codex/bin/codex.js']),
            ('/usr/bin/node', ['node', '/x/@wonderwhy-er/desktop-commander/dist/index.js', 'remote']),
            ('/usr/bin/bash', ['bash', '/x/run-codex.sh']),
            ('/usr/bin/remote_desktop_commander', ['remote_desktop_commander']),
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
        self.boot.write_text('22222222-2222-2222-2222-222222222222\n')
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
        names = ('bash', 'python3', 'dirname', 'cat', 'mkdir', 'stat', 'mktemp',
                 'mv', 'rm', 'chmod', 'cp', 'cmp', 'awk', 'flock', 'readlink')
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
        (isolated / 'flock').unlink()
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

    def test_hook_does_not_require_python(self):
        isolated = self.root / 'no-python'
        isolated.mkdir()
        for name in ('bash', 'dirname', 'cat', 'mkdir', 'stat', 'mktemp',
                     'mv', 'rm', 'chmod', 'cp', 'cmp', 'awk', 'flock', 'readlink'):
            (isolated / name).symlink_to(shutil.which(name))
        self.env['PATH'] = str(isolated)
        result = self.run_script('check-requirements.sh', 'hook')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('python3', result.stdout)
        self.assertNotIn('SKIP', result.stdout)
        tools = self.run_script('check-requirements.sh', 'tools')
        self.assertEqual(tools.returncode, 1)
        self.assertIn('python3: missing', tools.stdout)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse(self.calls.exists())

    def test_service_does_not_require_codex_or_python(self):
        isolated = self.root / 'service-only'
        isolated.mkdir()
        for name in ('bash', 'dirname', 'cat', 'mkdir', 'stat', 'mktemp',
                     'mv', 'rm', 'chmod', 'cp', 'cmp'):
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

    def test_update_independent_of_reminder_and_components(self):
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', self.finish(self.automatic()))
        receipts = self.home / '.local/state/codex-starter-kit'
        receipts.mkdir(parents=True, mode=0o700)
        (receipts / 'hook').write_text('0.0.1\n')
        output = self.finish(self.automatic())
        self.assertIn(b'Hook: 0.0.1', output)
        self.assertIn(b'COPY THIS PROMPT INTO CHATGPT', output)
        self.assertIn(b'Hook: 0.0.1', self.finish(self.automatic()))
        (receipts / 'service').write_text('0.0.2\n')
        self.assertIn(b'Service: 0.0.2', self.finish(self.automatic()))
        (self.bundle / 'version.txt').write_text('0.2.0\n')
        self.assertEqual(self.finish(self.automatic()).count(b'Setup outdated'), 1)

    def test_hook_idempotence_and_preservation(self):
        rc = self.home / '.bashrc'
        rc.write_text('# existing\nexport KEEP=yes\n')
        rc.chmod(0o640)
        self.assertEqual(self.run_script('setup.sh', '--install-hook').returncode, 0)
        first = rc.read_bytes()
        self.assertEqual(self.run_script('setup.sh', '--install-hook').returncode, 0)
        self.assertEqual(rc.read_bytes(), first)
        self.assertTrue(first.startswith(b'# existing\nexport KEEP=yes\n'))
        self.assertEqual(first.count(b'# BEGIN'), 1)
        self.assertEqual((self.home / '.local/state/codex-starter-kit/hook').read_text(), (self.bundle / 'version.txt').read_text())
        self.assertEqual(rc.stat().st_mode & 0o777, 0o640)
        subprocess.run(['bash', '-n', str(rc)], check=True)

    def test_service_preview_install_update(self):
        unit = self.home / '.config/systemd/user/desktop-commander.service'
        result = self.run_script('setup.sh')
        self.assertIn('managed unit v1', result.stdout)
        self.assertFalse(unit.exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.home / '.local/state/codex-starter-kit').exists())
        self.assertEqual(self.run_script('setup.sh', '--install-service').returncode, 0)
        self.assertEqual(self.calls.read_text(), '--user daemon-reload\n')
        self.run_script('setup.sh', '--install-service')
        self.assertEqual(self.calls.read_text(), '--user daemon-reload\n')
        unit.write_text('old installed unit\n')
        self.run_script('setup.sh', '--install-service')
        self.assertEqual(Path(str(unit) + '.backup').read_text(), 'old installed unit\n')
        self.assertEqual(self.calls.read_text(), '--user daemon-reload\n' * 2)
        self.assertIn('Restart=always', unit.read_text())
        self.assertIn('checkout with spaces', unit.read_text())


if __name__ == '__main__':
    unittest.main()
