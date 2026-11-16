"""Release checks and preconditions in disposable copies, never the working project."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'kit'
        self.root.mkdir()
        for name in ('release.sh', 'README.md', 'bootstrap', 'scripts'):
            src = SOURCE / name
            if src.is_dir():
                shutil.copytree(src, self.root / name)
            else:
                shutil.copy2(src, self.root / name)
        spec = importlib.util.spec_from_file_location('release', self.root / 'scripts/release.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def run_check(self, **env):
        clean = dict(os.environ)
        clean.pop('GITHUB_REF', None)
        clean.pop('CI_COMMIT_TAG', None)
        clean.update(env)
        return subprocess.run([str(self.root / 'release.sh'), '--check'],
                              capture_output=True, text=True, env=clean)

    def test_check_readonly_without_git(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.run_check().returncode, 0)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_shared_transform_and_marker_failures(self):
        for name, content in self.module.transform('2.3.4').items():
            (self.root / name).write_text(content)
        self.assertEqual(self.run_check().returncode, 0)
        path = self.root / 'README.md'
        original = path.read_text()
        path.write_text(original.replace('version: 2.3.4', 'version: 2.3.5'))
        self.assertNotEqual(self.run_check().returncode, 0)
        path.write_text(original.replace('<!-- codex-starter-kit version: 2.3.4 -->', ''))
        self.assertNotEqual(self.run_check().returncode, 0)
        path.write_text(original + '\n<!-- codex-starter-kit version: 2.3.4 -->\n')
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_tag_context_requires_literal_match(self):
        self.assertNotEqual(self.run_check(GITHUB_REF='refs/tags/v1.0.0').returncode, 0)
        self.assertNotEqual(self.run_check(CI_COMMIT_TAG='1.0.0').returncode, 0)  # No Git.

    def test_git_fixture_dirty_and_missing_upstream(self):
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                  capture_output=True, text=True)
        git('init', '-b', 'main')
        # A fixture commit only, never a release commit or a commit in the project.
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
            'commit', '-m', 'Fixture')
        before = (self.root / 'bootstrap/version.txt').read_bytes()
        result = subprocess.run([str(self.root / 'release.sh'), '0.2.0'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('DANGER', result.stdout)
        (self.root / 'dirty').write_text('untracked')
        result = subprocess.run([str(self.root / 'release.sh'), '0.2.0'], capture_output=True, text=True)
        self.assertIn('clean worktree', result.stderr)
        self.assertEqual(before, (self.root / 'bootstrap/version.txt').read_bytes())

    def test_confirmation_defaults_no_and_never_writes(self):
        # Mock Git reads only: exercise the entire preflight without creating a release.
        def git(*args):
            answers = {
                ('rev-parse', '--show-toplevel'): str(self.root),
                ('status', '--porcelain', '--untracked-files=all'): '',
                ('symbolic-ref', '--quiet', '--short', 'HEAD'): 'main',
                ('rev-parse', '--abbrev-ref', '@{upstream}'): 'origin/main',
                ('config', 'branch.main.remote'): 'origin',
                ('config', 'branch.main.merge'): 'refs/heads/main',
                ('remote', 'get-url', '--all', 'origin'): '/fixture/remote',
                ('remote', 'get-url', '--push', '--all', 'origin'): '/fixture/remote',
                ('rev-parse', 'HEAD'): 'abc',
                ('rev-parse', '@{upstream}'): 'abc',
                ('ls-remote', '--exit-code', 'origin', 'refs/heads/main'): 'abc\trefs/heads/main',
                ('var', 'GIT_AUTHOR_IDENT'): 'Test',
                ('var', 'GIT_COMMITTER_IDENT'): 'Test',
                ('tag', '--list', '0.2.0'): '',
                ('ls-remote', '--tags', 'origin', 'refs/tags/0.2.0'): '',
            }
            return answers[args]  # Any mutation is an unexpected call and fails.
        before = (self.root / 'bootstrap/version.txt').read_bytes()
        with patch.object(self.module, 'git', side_effect=git), \
                patch('sys.argv', ['release.sh', '0.2.0']), \
                patch('sys.stdin.isatty', return_value=True), \
                patch('builtins.input', return_value=''), \
                patch('builtins.print'):
            self.module.main()
        self.assertEqual(before, (self.root / 'bootstrap/version.txt').read_bytes())


if __name__ == '__main__':
    unittest.main()
