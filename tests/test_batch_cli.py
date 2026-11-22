"""Offline batch CLI tests with a copied bootstrap and private test state."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


BOOTSTRAP = Path(__file__).resolve().parents[1] / 'bootstrap'
FAKE_CODEX = """#!/usr/bin/env python3
from pathlib import Path
import os, sys, time
prompt = sys.stdin.read().strip()
if prompt != 'manual' and any(target.endswith(('.lock', '.active')) for fd in Path('/proc/self/fd').iterdir()
       if (target := os.path.realpath(fd))):
    raise SystemExit(97)
with open('order', 'a') as output:
    output.write(prompt + chr(10))
if prompt == 'wait':
    for _ in range(200):
        if Path('release').exists():
            break
        time.sleep(0.025)
    Path('child-finished').touch()
raise SystemExit(19 if prompt == 'fail' else 0)
"""


class BatchCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='kit-batch-tests-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.root / 'alias').symlink_to(self.project)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.tools = self.root / 'bundle/tools'
        shutil.copytree(BOOTSTRAP, self.tools.parent, ignore=shutil.ignore_patterns('__pycache__'))
        for filename, new in (
            ('project_lock.py', self.root / 'locks'),
            ('task_registry.py', self.root / 'registry'),
        ):
            new.mkdir(mode=0o700)
            path = self.tools / filename
            source = path.read_text()
            old = "Path('/tmp') / f'codex-starter-kit-"
            self.assertIn(old, source)
            path.write_text(source.replace(old, f"Path({str(new)!r}) / f'codex-starter-kit-"))
        registry = self.tools / 'task_registry.py'
        source = registry.read_text()
        self.assertIn("entries = os.listdir('/dev/pts')", source)
        registry.write_text(source.replace("entries = os.listdir('/dev/pts')", 'entries = []'))
        executable = self.root / 'codex'
        executable.write_text(FAKE_CODEX)
        executable.chmod(0o755)
        self.env = {'HOME': str(self.home), 'PATH': f'{self.root}:/usr/bin:/bin',
                    'CODEX_STARTER_KIT_NOTIFY_METADATA': '0',
                    'CODEX_STARTER_KIT_LIVE_INSPECT': '1'}
        self.store = self.home / '.codex-starter-kit-batches'

    def batch(self, *args, background=False):
        command = [str(self.tools / 'batch.sh'), *map(str, args)]
        options = dict(cwd=self.root, env=self.env, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if background:
            child = subprocess.Popen(command, **options)
            self.addCleanup(self.clean_child, child)
            return child
        return subprocess.run(command, **options, timeout=10)

    def clean_child(self, child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=8)

    def manual(self):
        return subprocess.run([str(self.tools / 'run-codex.sh'), 'alias', '--task', 'manual'],
                              cwd=self.root, env=dict(self.env, CODEX_STARTER_KIT_LIVE_INSPECT='0'),
                              capture_output=True, text=True, timeout=10)

    def manifest(self, *prompts):
        tasks = [dict(id=f'task-{index}', **(prompt if isinstance(prompt, dict) else {'prompt': prompt}))
                 for index, prompt in enumerate(prompts)]
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(dict(version=1, project='alias', tasks=tasks)))
        return path

    def batch_id(self, stdout):
        match = re.search(r'^BATCH_ID=([a-f0-9]{32})$', stdout, re.MULTILINE)
        self.assertIsNotNone(match, stdout)
        return match[1]

    def wait_for(self, path, content=None):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if path.exists() and (content is None or content in path.read_text()):
                return
            time.sleep(0.02)
        self.fail(f'Not observed: {path}')

    def test_sequence_resume_and_continuous_lock_between_tasks(self):
        path = self.tools / 'batch.py'
        path.write_text(path.read_text().replace(
            '                attempt.finish(result)',
            """                attempt.finish(result)
                if index == 0 and not cancelled():
                    import time
                    (project / 'between').touch()
                    while not cancelled():
                        time.sleep(0.02)"""))
        (self.root / 'prompt.txt').write_text('second')
        manifest = self.manifest('first', {'prompt_file': 'prompt.txt'})
        child = self.batch('run', manifest, background=True)
        self.wait_for(self.project / 'between')
        self.assertEqual(self.manual().returncode, 75)
        self.assertEqual(self.batch('run', manifest).returncode, 75)
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 143, (stdout, stderr))
        self.assertEqual(stderr.count('START Codex'), 1)
        self.assertEqual(stdout.count('SANDBOX_HANDOFF_EXIT_CODE=0'), 1)
        batch_id = self.batch_id(stdout)
        (self.root / 'prompt.txt').write_text('changed')
        manifest.unlink()
        resumed = self.batch('resume', batch_id)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(resumed.stderr.count('START Codex'), 1)
        self.assertEqual(self.batch('resume', batch_id).returncode, 0)
        status = json.loads(self.batch('status', batch_id).stdout)
        self.assertEqual(status['project'], str(self.project))
        self.assertEqual([row['status'] for row in status['tasks']], ['succeeded', 'succeeded'])
        self.assertEqual((self.project / 'order').read_text(), 'first\nsecond\n')
        self.assertEqual(self.manual().returncode, 0)

    def test_run_completes_two_tasks_with_one_notification_pair_each(self):
        result = self.batch('run', self.manifest('first', 'second'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count('START Codex'), 2)
        self.assertEqual(result.stdout.count('END Codex'), 2)
        self.assertEqual((self.project / 'order').read_text(), 'first\nsecond\n')

    def test_fail_fast_and_corrupt_receipt_or_state(self):
        result = self.batch('run', self.manifest('fail', 'never'))
        self.assertEqual(result.returncode, 19, result.stderr)
        batch_id = self.batch_id(result.stdout)
        self.assertEqual(self.batch('resume', batch_id).returncode, 19)
        directory = self.store / batch_id
        (directory / f'{batch_id}-1.json').write_text('{}')
        status = json.loads(self.batch('status', batch_id).stdout)
        self.assertEqual([row['status'] for row in status['tasks']], ['unknown', 'pending'])
        self.assertEqual(self.batch('resume', batch_id).returncode, 76)
        (directory / 'state.json').write_text('{')
        self.assertEqual(self.batch('resume', batch_id).returncode, 2)
        self.assertEqual((self.project / 'order').read_text(), 'fail\n')

    def test_kill_preserves_fence_and_never_replays_missing_receipt(self):
        child = self.batch('run', self.manifest('wait', 'never'), background=True)
        self.wait_for(self.project / 'order', 'wait')
        child.kill()
        (self.project / 'release').touch()
        stdout, _ = child.communicate(timeout=8)
        self.wait_for(self.project / 'child-finished')
        batch_id = self.batch_id(stdout)
        self.assertEqual(json.loads(self.batch('status', batch_id).stdout)['status'], 'unknown')
        self.assertEqual(self.batch('resume', batch_id).returncode, 76)
        self.assertEqual(self.manual().returncode, 76)
        self.assertEqual((self.project / 'order').read_text(), 'wait\n')

    def test_kill_after_codex_exit_but_before_receipt_does_not_replay(self):
        path = self.tools / 'batch.py'
        path.write_text(path.read_text().replace(
            '                attempt.finish(result)',
            """                import time
                (project / 'before-receipt').touch()
                time.sleep(20)
                attempt.finish(result)"""))
        child = self.batch('run', self.manifest('first', 'never'), background=True)
        self.wait_for(self.project / 'before-receipt')
        child.kill()
        stdout, _ = child.communicate(timeout=8)
        batch_id = self.batch_id(stdout)
        self.assertEqual(self.batch('resume', batch_id).returncode, 76)
        self.assertEqual((self.project / 'order').read_text(), 'first\n')
        # Quiescence was proved before the crash; only the batch is unknown.
        self.assertEqual(self.manual().returncode, 0)

    def test_term_during_task_publishes_cancellation(self):
        child = self.batch('run', self.manifest('wait', 'never'), background=True)
        self.wait_for(self.project / 'order', 'wait')
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 143, (stdout, stderr))
        batch_id = self.batch_id(stdout)
        receipt = json.loads(self.batch('status', batch_id).stdout)['tasks'][0]['result']
        self.assertEqual((receipt['kind'], receipt['signal']), ('cancelled', 15))
        self.assertEqual(self.batch('resume', batch_id).returncode, 143)
        self.assertEqual((self.project / 'order').read_text(), 'wait\n')
        self.assertEqual(self.manual().returncode, 0)

    def test_invalid_manifest_and_fifo_prompt_fail_before_launch(self):
        os.mkfifo(self.root / 'fifo')
        result = self.batch('run', self.manifest({'prompt_file': 'fifo'}))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.store.exists())
        self.assertFalse((self.project / 'order').exists())
