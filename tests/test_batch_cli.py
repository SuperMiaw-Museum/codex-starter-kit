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
import json, os, sys, time
if '--help' in sys.argv:
    print(' --json ')
    raise SystemExit(0)
prompt = sys.stdin.read().strip()
if any(target.endswith(('.lock', '.active')) for fd in Path('/proc/self/fd').iterdir()
       if (target := os.path.realpath(fd))) and prompt != 'manual':
    raise SystemExit(97)
if '--json' in sys.argv:
    print(json.dumps({'type': 'thread.started',
                      'thread_id': '12345678-1234-1234-1234-123456789abc'}), flush=True)
with open('order', 'a') as output:
    output.write(prompt + chr(10))
if prompt.startswith('wait'):
    Path(prompt + '.supervisor').write_text(str(os.getppid()))
    deadline = time.monotonic() + 7
    while not Path(prompt + '.release').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    Path(prompt + '.done').touch()
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
        bundle = self.root / 'bundle'
        self.internal = bundle / 'internal'
        self.orchestration = bundle / 'orchestration'
        shutil.copytree(BOOTSTRAP, bundle, ignore=shutil.ignore_patterns('__pycache__'))
        for filename, new in (
            ('project_lock.py', self.root / 'locks'),
            ('task_registry.py', self.root / 'registry'),
        ):
            new.mkdir(mode=0o700)
            path = self.internal / filename
            source = path.read_text()
            old = "Path('/tmp') / f'codex-starter-kit-"
            self.assertIn(old, source)
            path.write_text(source.replace(old, f"Path({str(new)!r}) / f'codex-starter-kit-"))
        registry = self.internal / 'task_registry.py'
        source = registry.read_text()
        self.assertIn("entries = os.listdir('/dev/pts')", source)
        registry.write_text(source.replace("entries = os.listdir('/dev/pts')", 'entries = []'))
        executable = self.root / 'codex'
        executable.write_text(FAKE_CODEX)
        executable.chmod(0o755)
        self.env = {'HOME': str(self.home), 'PATH': f'{self.root}:/usr/bin:/bin',
                    'CODEX_STARTER_KIT_NOTIFY_METADATA': '1',
                    'CODEX_STARTER_KIT_BATCH_HOST_MAX_PARALLEL': '2',
                    'CODEX_STARTER_KIT_LIVE_INSPECT': '1'}
        self.store = self.home / '.codex-starter-kit-batches'
        self.other = self.root / 'other'
        self.third = self.root / 'third'
        self.other.mkdir()
        self.third.mkdir()

    def batch(self, *args, background=False):
        command = [str(self.orchestration / 'batch.sh'), *map(str, args)]
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

    def test_snapshot_and_reservation_between_tasks(self):
        module = self.internal / 'batch_scheduler.py'
        source = module.read_text()
        boundary = "                graph.done(row['id'])\n"
        self.assertIn(boundary, source)
        module.write_text(source.replace(boundary, boundary + """                if rank == 0:
                    (directory / 'between').touch()
                    while not cancelled():
                        time.sleep(0.02)
""", 1))
        prompt = self.root / 'prompt.txt'
        prompt.write_text('second')
        manifest = self.manifest(('first', 'alias', 'first'),
                                 ('second', 'alias', {'prompt_file': 'prompt.txt'}, ['first']))
        child = self.batch('run', manifest, background=True)
        batch_id = self.batch_id(child.stdout.readline())
        self.wait_for(self.store / batch_id / 'between')
        self.assertEqual(self.manual('alias').returncode, 75)
        self.assertEqual(self.batch('run', manifest).returncode, 75)
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 143, (stdout, stderr))
        self.assertEqual(stderr.count('START Codex'), 1)
        self.assertEqual(stdout.count('END Codex'), 1)
        prompt.write_text('changed')
        manifest.unlink()
        resumed = self.batch('resume', batch_id)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(resumed.stderr.count('START Codex'), 1)
        self.assertEqual(resumed.stdout.count('END Codex'), 1)
        self.assertEqual(self.batch('resume', batch_id).returncode, 0)
        self.assertEqual((self.project / 'order').read_text(), 'first\nsecond\n')
        self.assertEqual(self.manual('alias').returncode, 0)

    def test_kill_after_codex_exit_but_before_receipt_does_not_replay(self):
        module = self.internal / 'batch_scheduler.py'
        source = module.read_text()
        boundary = '    attempt.finish(result)\n'
        self.assertIn(boundary, source)
        module.write_text(source.replace(boundary, """    (directory / 'worker-pid').write_text(str(os.getpid()))
    (directory / 'worker-pid').rename(directory / 'before-receipt')
    time.sleep(7)
""" + boundary, 1))
        child = self.start(('first', 'alias', 'first'), ('next', 'alias', 'never', ['first']))
        batch_id = self.batch_id(child.stdout.readline())
        marker = self.store / batch_id / 'before-receipt'
        self.wait_for(marker)
        os.kill(int(marker.read_text()), signal.SIGKILL)
        child.communicate(timeout=8)
        self.assertEqual(child.returncode, 76)
        self.assertEqual(self.batch('resume', batch_id).returncode, 76)
        self.assertEqual((self.project / 'order').read_text(), 'first\n')
        self.assertEqual(self.manual('alias').returncode, 0)

    def test_fifo_prompt_fails_before_launch(self):
        os.mkfifo(self.root / 'fifo')
        manifest = self.manifest(('a', 'alias', {'prompt_file': 'fifo'}))
        self.assertEqual(self.batch('run', manifest).returncode, 2)
        self.assertFalse(self.store.exists())
        self.assertFalse((self.project / 'order').exists())

    def manifest(self, *tasks, parallel=2, name='manifest'):
        path = self.root / (name + '.json')
        path.write_text(json.dumps(dict(version=2, max_parallel=parallel, tasks=[
            dict(id=task_id, project=project, **({'depends_on': deps} if deps else {}),
                 **(prompt if isinstance(prompt, dict) else {'prompt': prompt}))
            for task_id, project, prompt, *deps_list in tasks
            for deps in [deps_list[0] if deps_list else []]
        ])))
        return path

    def start(self, *tasks, **options):
        proc = self.batch('run', self.manifest(*tasks, **options), background=True)
        self.addCleanup(self.release_waits)
        return proc

    def release_waits(self):
        for directory in (self.project, self.other, self.third):
            for marker in directory.glob('wait*.supervisor'):
                marker.with_suffix('.release').touch()

    def manual(self, project):
        return subprocess.run([str(self.orchestration / 'run-codex.sh'), str(project),
                               '--task', 'manual'], cwd=self.root,
                              env=dict(self.env, CODEX_STARTER_KIT_LIVE_INSPECT='0'),
                              capture_output=True, text=True, timeout=10)

    def report(self, batch_id):
        report = self.batch('status', batch_id)
        self.assertEqual(report.returncode, 0, report.stderr)
        return json.loads(report.stdout)

    def link(self, mode, name, target):
        directory = self.home / ('.codex-starter-kit-repos-' + mode)
        directory.mkdir(exist_ok=True)
        link = directory / name
        link.symlink_to(target)
        return link

    def test_v1_manifest_and_existing_state_are_rejected_without_mutation(self):
        legacy = dict(version=1, project=str(self.project), tasks=[dict(id='a', prompt='never')])
        path = self.root / 'legacy.json'
        path.write_text(json.dumps(legacy))
        result = self.batch('run', path)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Unsupported batch manifest version 1', result.stderr)
        self.assertFalse(self.store.exists())

        batch_id = 'a' * 32
        directory = self.store / batch_id
        self.store.mkdir(mode=0o700)
        directory.mkdir(mode=0o700)
        files = {'state.json': dict(batch_id=batch_id, manifest=legacy, started=1),
                 f'{batch_id}-1.json.started': dict(version=1, attempt_id=f'{batch_id}-1',
                                                     project=str(self.project))}
        for name, payload in files.items():
            path = directory / name
            path.write_text(json.dumps(payload))
            path.chmod(0o600)
        for command in ('status', 'resume'):
            result = self.batch(command, batch_id)
            self.assertEqual(result.returncode, 2)
            self.assertIn('Unsupported batch state version 1', result.stderr)
            self.assertEqual({path.name: json.loads(path.read_text()) for path in directory.iterdir()},
                             files)
        self.assertFalse((self.project / 'order').exists())

    def test_invalid_graphs_and_limits_are_rejected_before_publication(self):
        graphs = [
            [('a', 'project', 'x', ['a'])],
            [('a', 'project', 'x', ['b']), ('b', 'other', 'x', ['a'])],
            [('a', 'project', 'x', ['missing'])],
            [('a', 'project', 'x', ['b', 'b']), ('b', 'other', 'x')],
            [('a', 'project', 'x'), ('a', 'other', 'x')],
        ]
        for graph in graphs:
            with self.subTest(graph=graph):
                self.assertEqual(self.batch('run', self.manifest(*graph)).returncode, 2)
        for cap in (0, True, 1.5):
            self.assertEqual(self.batch('run', self.manifest(('a', 'project', 'x'),
                                                            parallel=cap)).returncode, 2)
        self.assertFalse(self.store.exists())

    def test_graph_priority_alias_and_skip_blocked_ready_task(self):
        child = self.start(('dependent', 'alias', 'dependent', ['first', 'independent']),
                           ('first', 'project', 'wait-first'), ('blocked', 'alias', 'blocked'),
                           ('independent', 'other', 'wait-other'))
        self.wait_for(self.project / 'wait-first.supervisor')
        self.wait_for(self.other / 'wait-other.supervisor')
        self.assertEqual((self.project / 'order').read_text(), 'wait-first\n')
        self.assertEqual(self.manual('alias').returncode, 75)
        (self.other / 'wait-other.release').touch()
        batch_id, = os.listdir(self.store)
        self.wait_for(self.store / batch_id / f'{batch_id}-4.json')
        (self.project / 'wait-first.release').touch()
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertEqual((self.project / 'order').read_text(), 'wait-first\ndependent\nblocked\n')
        self.assertEqual(stderr.count('START Codex'), 4)
        self.assertEqual(stdout.count('END Codex'), 4)
        batch_id = self.batch_id(stdout)
        self.assertEqual(self.batch('resume', batch_id).returncode, 0)
        rows = self.report(batch_id)['tasks']
        self.assertEqual([row['status'] for row in rows], ['succeeded'] * 4)
        self.assertTrue(all(row['session_id'] == '12345678-1234-1234-1234-123456789abc'
                            for row in rows))

    def test_global_host_cap_and_continuous_reservation(self):
        self.env['CODEX_STARTER_KIT_BATCH_HOST_MAX_PARALLEL'] = '1'
        first = self.start(('a', 'project', 'wait-first'))
        self.wait_for(self.project / 'wait-first.supervisor')
        self.env['CODEX_STARTER_KIT_BATCH_HOST_MAX_PARALLEL'] = '2'
        incompatible = self.batch('run', self.manifest(('x', 'third', 'never')))
        self.assertEqual(incompatible.returncode, 75, incompatible.stderr)
        self.env['CODEX_STARTER_KIT_BATCH_HOST_MAX_PARALLEL'] = '1'
        second = self.start(('b', 'other', 'wait-second'), ('c', 'third', 'wait-third'))
        deadline = time.monotonic() + 5
        while len(list(self.store.iterdir())) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(len(list(self.store.iterdir())), 2)
        self.assertEqual(self.manual('third').returncode, 75)
        self.assertFalse((self.other / 'order').exists())
        (self.project / 'wait-first.release').touch()
        first.communicate(timeout=10)
        self.wait_for(self.other / 'wait-second.supervisor')
        self.assertFalse((self.third / 'order').exists())
        self.release_waits()
        self.wait_for(self.third / 'wait-third.supervisor')
        self.release_waits()
        stdout, stderr = second.communicate(timeout=10)
        self.assertEqual(second.returncode, 0, (stdout, stderr))

    def test_shared_external_and_writer_contention(self):
        repository = self.root / 'z-external'
        repository.mkdir()
        writable = self.link('rw', 'first', repository)
        readonly = self.link('ro', 'alias', repository)
        child = self.start(('a', 'project', 'wait-first'), ('b', 'other', 'wait-second'))
        self.wait_for(self.project / 'wait-first.supervisor')
        self.wait_for(self.other / 'wait-second.supervisor')
        contrary = self.manual(repository)
        self.assertEqual(contrary.returncode, 2, contrary.stderr)
        readonly.unlink()
        blocked = self.manual('third')
        self.assertEqual(blocked.returncode, 75, blocked.stderr)
        contender = self.manifest(('contender', 'third', 'never'), name='contender')
        self.assertEqual(self.batch('run', contender).returncode, 75)
        readonly.symlink_to(repository)
        self.assertEqual(self.manual('third').returncode, 0)
        self.release_waits()
        self.assertEqual(child.communicate(timeout=10)[1].count('START Codex'), 2)
        self.assertEqual(child.returncode, 0)
        readonly.unlink()
        writer = self.start(('a', 'project', 'wait-writer'), ('b', 'other', 'second'))
        self.wait_for(self.project / 'wait-writer.supervisor')
        self.assertEqual((self.other / 'order').read_text(), 'wait-second\n')
        self.assertEqual(self.manual('third').returncode, 75)
        self.release_waits()
        writer.communicate(timeout=10)
        self.assertEqual(writer.returncode, 0)
        writable.unlink()

    def test_nested_worktree_and_resource_drift(self):
        nested = self.project / 'nested'
        nested.mkdir()
        rejected = self.batch('run', self.manifest(('a', 'project', 'x'),
                                                   ('b', 'project/nested', 'y')))
        self.assertEqual(rejected.returncode, 2, rejected.stderr)
        (self.other / '.git').write_text('gitdir: /unavailable/worktree\n')
        rejected = self.batch('run', self.manifest(('a', 'other', 'x')))
        self.assertEqual(rejected.returncode, 2, rejected.stderr)
        (self.other / '.git').unlink()
        child = self.start(('a', 'project', 'wait-first'), ('b', 'other', 'never', ['a']))
        self.wait_for(self.project / 'wait-first.supervisor')
        self.assertEqual(self.manual(nested).returncode, 75)
        self.link('ro', 'third', self.third)
        self.release_waits()
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 2, stderr)
        rows = self.report(self.batch_id(stdout))['tasks']
        self.assertEqual([row['status'] for row in rows], ['succeeded', 'failed'])
        self.assertFalse((self.other / 'order').exists())

    def test_failure_drains_without_admitting_dependants(self):
        child = self.start(('a', 'project', 'wait-first'), ('b', 'other', 'fail'),
                           ('c', 'third', 'never', ['a']))
        self.wait_for(self.project / 'wait-first.supervisor')
        self.wait_for(self.other / 'order', 'fail')
        self.assertIsNone(child.poll())
        self.release_waits()
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 19, stderr)
        batch_id = self.batch_id(stdout)
        self.assertEqual(self.batch('resume', batch_id).returncode, 19)
        self.assertEqual([row['status'] for row in self.report(batch_id)['tasks']],
                         ['succeeded', 'failed', 'pending'])
        self.assertFalse((self.third / 'order').exists())

    def test_explicit_signal_reaches_all_supervisors(self):
        child = self.start(('a', 'project', 'wait-first'), ('b', 'other', 'wait-second'),
                           ('c', 'third', 'never', ['a']))
        self.wait_for(self.project / 'wait-first.supervisor')
        self.wait_for(self.other / 'wait-second.supervisor')
        batch_id, = os.listdir(self.store)
        self.assertEqual(self.batch('resume', batch_id).returncode, 75)
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 143, (stdout, stderr))
        rows = self.report(batch_id)['tasks']
        self.assertEqual([row['status'] for row in rows], ['failed', 'failed', 'pending'])
        self.assertEqual([row['result']['signal'] for row in rows[:2]], [15, 15])
        self.assertEqual(self.batch('resume', batch_id).returncode, 143)
        self.assertEqual(self.manual('project').returncode, 0)

    def test_parent_crash_keeps_worker_slot_and_reservation_then_resumes(self):
        child = self.start(('a', 'project', 'wait-first'), ('b', 'other', 'later', ['a']))
        self.wait_for(self.project / 'wait-first.supervisor')
        batch_id, = os.listdir(self.store)
        child.kill()
        child.wait(timeout=5)
        self.assertEqual(self.batch('resume', batch_id).returncode, 75)
        self.assertEqual(self.manual('other').returncode, 75)
        self.env['CODEX_STARTER_KIT_BATCH_HOST_MAX_PARALLEL'] = '1'
        challenger = self.batch('run', self.manifest(('c', 'third', 'never')))
        self.assertEqual(challenger.returncode, 75, challenger.stderr)
        self.assertFalse((self.third / 'order').exists())
        self.release_waits()
        child.communicate(timeout=10)
        self.assertEqual([row['status'] for row in self.report(batch_id)['tasks']],
                         ['succeeded', 'pending'])
        result = self.batch('resume', batch_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.project / 'order').read_text(), 'wait-first\n')
        self.assertEqual((self.other / 'order').read_text(), 'later\n')

    def test_worker_crash_fences_reader_and_retains_session(self):
        external = self.root / 'external'
        external.mkdir()
        link = self.link('ro', 'external', external)
        child = self.start(('a', 'project', 'wait-first'), ('b', 'other', 'never', ['a']))
        marker = self.project / 'wait-first.supervisor'
        self.wait_for(marker)
        batch_id, = os.listdir(self.store)
        self.wait_for(self.store / batch_id / f'{batch_id}-1.json.session')
        os.kill(int(marker.read_text()), signal.SIGKILL)
        self.release_waits()
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 76, (stdout, stderr))
        self.assertEqual(self.batch('resume', batch_id).returncode, 76)
        row = self.report(batch_id)['tasks'][0]
        self.assertEqual(row['status'], 'unknown')
        self.assertIn('session_id', row)
        self.assertEqual(self.manual('third').returncode, 76)
        link.unlink()
        self.assertEqual(self.manual(external).returncode, 76)
        self.assertFalse((self.other / 'order').exists())

    def test_incomplete_receipt_is_unknown(self):
        result = self.batch('run', self.manifest(('a', 'project', 'first')))
        self.assertEqual(result.returncode, 0, result.stderr)
        batch_id = self.batch_id(result.stdout)
        receipt = self.store / batch_id / f'{batch_id}-1.json'
        record = json.loads(receipt.read_text())
        for invalid in (dict(record, version=1),
                        dict(record, task_id='foreign'),
                        dict(record, result={'kind': 'codex', 'exit_code': 0}),
                        dict(record, result={'kind': 'codex', 'exit_code': 0, 'process_exit': 0})):
            receipt.write_text(json.dumps(invalid))
            self.assertEqual(self.report(batch_id)['status'], 'unknown')
            self.assertEqual(self.batch('resume', batch_id).returncode, 76)
        self.assertEqual((self.project / 'order').read_text(), 'first\n')
        (receipt.parent / 'state.json').write_text('{')
        for command in ('status', 'resume'):
            self.assertEqual(self.batch(command, batch_id).returncode, 2)

    def test_manifest_limit_applies_below_host_limit(self):
        child = self.start(('a', 'project', 'wait-first'), ('b', 'other', 'wait-second'),
                           parallel=1)
        self.wait_for(self.project / 'wait-first.supervisor')
        self.assertFalse((self.other / 'order').exists())
        self.release_waits()
        self.wait_for(self.other / 'wait-second.supervisor')
        self.release_waits()
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, (stdout, stderr))

    def test_parent_crash_between_intention_and_supervisor(self):
        module = self.internal / 'batch_scheduler.py'
        original = module.read_text()
        boundary = '                        attempt.claim()\n'
        self.assertIn(boundary, original)
        module.write_text(original.replace(
            boundary, boundary + """                        (directory / 'before-spawn').touch()
                        time.sleep(120)
""", 1))
        child = self.start(('a', 'project', 'never'))
        batch_id = self.batch_id(child.stdout.readline())
        self.wait_for(self.store / batch_id / 'before-spawn')
        child.kill()
        child.communicate(timeout=5)
        report = self.report(batch_id)
        self.assertEqual(report['tasks'][0]['status'], 'unknown')
        self.assertNotIn('result', report['tasks'][0])
        self.assertEqual(self.batch('resume', batch_id).returncode, 76)
        self.assertFalse((self.project / 'order').exists())
        self.assertEqual(self.manual('project').returncode, 0)
