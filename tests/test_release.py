"""Exercise the Bash release only in disposable repositories and bare remotes."""
from pathlib import Path
import errno
import os
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1]
VERSION_FILE = 'bootstrap/version.txt'
FILES = (VERSION_FILE, 'release.sh', 'bootstrap/setup.sh',
         'bootstrap/remote-control.sh', 'README.md', 'bootstrap/PROMPT.md')


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'kit'
        self.root.mkdir()
        for name in ('release.sh', 'README.md', 'bootstrap'):
            src = SOURCE / name
            if src.is_dir():
                shutil.copytree(src, self.root / name)
            else:
                shutil.copy2(src, self.root / name)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                        GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0')
        for key in list(self.env):
            if key.startswith(('GIT_', 'GITHUB_', 'CI_COMMIT_TAG')) and key not in (
                    'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_TERMINAL_PROMPT'):
                self.env.pop(key)
        self.version = (self.root / VERSION_FILE).read_text().strip()

    def run_cli(self, *args, **env):
        return subprocess.run(['./release.sh', *args], cwd=self.root,
                              env=dict(self.env, **env), capture_output=True, text=True)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root,
                                       env=self.env, stderr=subprocess.PIPE, text=True).strip()

    def fixture(self):
        self.remote = Path(self.tmp.name) / 'origin.git'
        self.git('init', '--bare', str(self.remote))
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('add', '.')
        self.git('commit', '-m', 'Fixture')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')
        self.initial = self.git('rev-parse', 'HEAD')

    def interactive(self, args=(), answers='yes\n'):
        master, slave = pty.openpty()
        proc = subprocess.Popen(['./release.sh', *args], cwd=self.root, env=self.env,
                                stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        os.write(master, answers.encode())
        output = b''
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    output += chunk
            else:
                proc.kill()
                self.fail('Release timed out')
            return proc.wait(timeout=2), output.decode()
        finally:
            os.close(master)
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def snapshot(self):
        return {name: (self.root / name).read_bytes() for name in FILES}

    def test_check_readonly_without_git(self):
        before = self.snapshot()
        self.assertEqual(self.run_cli('--check').returncode, 0)
        self.assertEqual(before, self.snapshot())

    def test_version_file_validated(self):
        for content in ('01.2.3\n', self.version, self.version + '\n\n',
                        'v' + self.version + '\n', self.version + '\r\n'):
            with self.subTest(content=content):
                (self.root / VERSION_FILE).write_text(content)
                self.assertNotEqual(self.run_cli('--check').returncode, 0)

    def test_arguments_and_terminal(self):
        self.assertEqual(self.run_cli('--help').returncode, 0)
        for args in (('patch',), ('1.2.3',), ('--draft', self.version),
                     ('--bump',), ('--bump', 'bad'), ('--check', 'extra'),
                     ('--bump', 'patch', '--yes')):
            self.assertEqual(self.run_cli(*args).returncode, 2)
        self.assertIn('TTY', self.run_cli('--bump', 'patch').stderr)

    def test_tag_context(self):
        self.fixture()
        self.assertNotEqual(self.run_cli('--check', CI_COMMIT_TAG=self.version).returncode, 0)
        self.assertNotEqual(self.run_cli('--check', GITHUB_REF='refs/tags/').returncode, 0)
        self.git('tag', '-a', self.version, '-m', self.version)
        self.assertEqual(self.run_cli('--check', CI_COMMIT_TAG=self.version).returncode, 0)
        self.assertNotEqual(self.run_cli('--check', GITHUB_REF='refs/tags/v' + self.version).returncode, 0)
        self.assertEqual(self.run_cli('--check', GITHUB_REF='refs/tags/' + self.version,
                                     CI_COMMIT_TAG='wrong').returncode, 0)
        self.git('commit', '--allow-empty', '-m', 'Later')
        self.assertNotEqual(self.run_cli('--check', CI_COMMIT_TAG=self.version).returncode, 0)

    def test_decline_and_invalid_choice(self):
        self.fixture()
        before = self.snapshot()
        for args, answer in ((('--bump', 'patch'), '\n'),
                             (('--bump', 'patch'), 'anything\n'), ((), 'invalid\n'),
                             ((), 'minor\nn\n')):
            code, output = self.interactive(args, answer)
            self.assertEqual(code, 1 if answer == 'invalid\n' else 0, output)
            self.assertEqual(before, self.snapshot())
            self.assertEqual(self.git('rev-parse', 'HEAD'), self.initial)
            self.assertEqual(self.git('tag'), '')

    def test_bumps_commit_tag_and_atomic_push(self):
        self.fixture()
        major, minor, patch = map(int, self.version.split('.'))
        targets = [('patch', f'{major}.{minor}.{patch + 1}'),
                   ('minor', f'{major}.{minor + 1}.0'), ('major', f'{major + 1}.0.0')]
        for level, target in targets:
            code, output = self.interactive((), level + '\n YES \n')
            self.assertEqual(code, 0, output)
            self.assertIn('Proceed with release? [y/N]:', output)
            self.assertIn(f'Pushing main and {target} atomically...', output)
            self.assertEqual(self.git('cat-file', '-t', target), 'tag')
            self.assertEqual(self.git('for-each-ref', '--format=%(contents)', 'refs/tags/' + target), target)
            self.assertEqual(self.git('log', '-1', '--format=%s'), 'build: bump version')
            self.assertEqual(set(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()), {VERSION_FILE})
            self.assertEqual(self.git('--git-dir=' + str(self.remote), 'rev-parse', 'main'), self.git('rev-parse', 'HEAD'))
            self.assertEqual(self.git('--git-dir=' + str(self.remote), 'rev-parse', target), self.git('rev-parse', target))
            self.assertEqual(self.run_cli('--check').returncode, 0)
            self.assertEqual(self.git('status', '--porcelain'), '')
            self.assertTrue(os.access(self.root / 'release.sh', os.X_OK))

    def test_dirty_wrong_branch_upstream_and_remote_state(self):
        self.fixture()
        before = self.snapshot()
        (self.root / 'untracked').touch()
        code, output = self.interactive(('--bump', 'patch'))
        self.assertNotEqual(code, 0)
        self.assertIn('clean worktree', output)
        (self.root / 'untracked').unlink()
        self.git('checkout', '-b', 'other')
        self.assertNotEqual(self.interactive(('--bump', 'patch'))[0], 0)
        self.git('checkout', 'main')
        self.git('branch', '--unset-upstream')
        self.assertNotEqual(self.interactive(('--bump', 'patch'))[0], 0)
        self.git('branch', '--set-upstream-to=origin/main')
        self.git('config', 'remote.origin.pushurl', '/different/destination')
        self.assertNotEqual(self.interactive(('--bump', 'patch'))[0], 0)
        self.git('config', '--unset', 'remote.origin.pushurl')
        self.git('commit', '--allow-empty', '-m', 'Ahead')
        self.assertNotEqual(self.interactive(('--bump', 'patch'))[0], 0)
        self.assertEqual(before, self.snapshot())

    def test_existing_local_and_remote_tags_never_move(self):
        self.fixture()
        parts = self.version.split('.')
        parts[-1] = str(int(parts[-1]) + 1)
        target = '.'.join(parts)
        self.git('tag', target)
        original = self.git('rev-parse', target)
        self.assertNotEqual(self.interactive(('--bump', 'patch'))[0], 0)
        self.assertEqual(self.git('rev-parse', target), original)
        self.git('push', 'origin', target)
        self.git('tag', '-d', target)  # Fixture only: test the remote-only collision.
        self.assertNotEqual(self.interactive(('--bump', 'patch'))[0], 0)
        self.assertEqual(self.git('--git-dir=' + str(self.remote), 'rev-parse', target), original)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.initial)

    def test_rejected_atomic_push_keeps_local_state(self):
        self.fixture()
        hook = self.remote / 'hooks/update'
        hook.write_text('#!/bin/sh\ncase "$1" in refs/tags/*) exit 1;; esac\n')
        hook.chmod(0o755)
        code, output = self.interactive(('--bump', 'patch'))
        self.assertNotEqual(code, 0, output)
        self.assertIn('No rollback performed', output)
        self.assertNotEqual(self.git('rev-parse', 'HEAD'), self.initial)
        self.assertTrue(self.git('tag'))
        self.assertEqual(self.git('--git-dir=' + str(self.remote), 'rev-parse', 'main'), self.initial)
        self.assertEqual(self.git('--git-dir=' + str(self.remote), 'tag'), '')

    def test_bump_across_version_length(self):
        (self.root / VERSION_FILE).write_text('9.9.9\n')
        self.fixture()
        code, output = self.interactive(('--bump', 'major'))
        self.assertEqual(code, 0, output)
        self.assertEqual(self.git('cat-file', '-t', '10.0.0'), 'tag')
        self.assertEqual(self.run_cli('--check').returncode, 0)

    def test_commit_and_tag_failure_stop_push(self):
        self.fixture()
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        code, output = self.interactive(('--bump', 'patch'))
        self.assertNotEqual(code, 0, output)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.initial)
        self.assertEqual(self.git('tag'), '')
        self.assertTrue(self.git('status', '--porcelain'))
        hook.unlink()
        # Restore only this disposable fixture to exercise tag failure separately.
        self.git('reset', '--hard', self.initial)
        self.git('config', 'tag.gpgSign', 'true')
        self.git('config', 'gpg.program', '/nonexistent/test-gpg')
        code, output = self.interactive(('--bump', 'patch'))
        self.assertNotEqual(code, 0, output)
        self.assertNotEqual(self.git('rev-parse', 'HEAD'), self.initial)
        self.assertEqual(self.git('tag'), '')
        self.assertEqual(self.git('--git-dir=' + str(self.remote), 'rev-parse', 'main'), self.initial)

    def test_workflow_owns_only_draft_path(self):
        workflow = (SOURCE / '.github/workflows/check.yml').read_text()
        for text in ('needs: check', "github.ref_type == 'tag'", '--draft',
                     'gh api --paginate', 'cancel-in-progress: false',
                     'GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}'):
            self.assertIn(text, workflow)
        self.assertNotIn('actions/github-script', workflow)
        self.assertFalse((SOURCE / 'scripts/release.py').exists())

    def test_draft_workflow_reruns_and_lookup_failure(self):
        workflow = (SOURCE / '.github/workflows/check.yml').read_text()
        script = workflow.split('        run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in script.splitlines())
        stub = '''gh() {
          printf '%s\\n' "$*" >> "$GH_LOG"
          if [ "$1" = api ]; then
            printf '%s\\n' "$RELEASE_TAGS"
            return "$LOOKUP_STATUS"
          fi
        }
'''
        for label, tags, status, creates in (
                ('absent', '1.0.1\n1.2.30', '0', True),
                ('existing draft', '1.0.1\n1.2.3', '0', False),
                ('existing published', '1.2.3\n2.0.0', '0', False),
                ('lookup failure', '', '1', False)):
            with self.subTest(label=label):
                log = self.root / 'gh.log'
                log.write_text('')
                result = subprocess.run(
                    ['bash', '-e', '-o', 'pipefail', '-c', stub + script],
                    env=dict(self.env, GH_LOG=str(log), GH_REPO='owner/repo',
                             GITHUB_REF_NAME='1.2.3', RELEASE_TAGS=tags,
                             LOOKUP_STATUS=status), capture_output=True, text=True)
                calls = log.read_text().splitlines()
                self.assertEqual(result.returncode, int(status), result.stderr)
                self.assertEqual(calls[0],
                                 'api --paginate repos/owner/repo/releases --jq .[].tag_name')
                self.assertEqual(calls[1:], [
                    'release create 1.2.3 --draft --title 1.2.3 --generate-notes'
                ] if creates else [])


if __name__ == '__main__':
    unittest.main()
