"""Offline tests for the manifest and its durable state primitive."""
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[1] / 'bootstrap/tools'


class BatchStateTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('batch_state', TOOLS / 'batch_state.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(TOOLS), *sys.path]):
            spec.loader.exec_module(self.module)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_manifest_resolves_alias_and_keeps_task_order(self):
        project = self.root / 'repo'
        project.mkdir()
        (self.root / 'alias').symlink_to(project, target_is_directory=True)
        tasks = [{'id': 'b', 'prompt': 'First'}, {'id': 'a', 'prompt': 'Second'}]
        raw = json.dumps(dict(version=1, project='alias', tasks=tasks))
        self.assertEqual(self.module.parse_manifest(raw, base=self.root),
                         dict(version=1, project=str(project), tasks=tasks))

    def test_manifest_refuses_unsupported_or_ambiguous_input(self):
        for raw in (
            '{"version":1,"version":1,"project":".","tasks":[]}',
            '{"version":true,"project":".","tasks":[{"id":"a","prompt":"x"}]}',
            '{"version":1,"project":".","tasks":[]}',
            '{"version":1,"project":".","tasks":[{"id":"a","prompt":"x"},{"id":"a","prompt":"y"}]}',
            '{"version":1,"project":".","tasks":[{"id":"a","prompt":"x","mode":"read"}]}',
            '{"version":1,"project":".","tasks":[{"id":"../a","prompt":"x"}]}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.module.parse_manifest(raw, base=self.root)

    def test_atomic_state_replacement_and_failure(self):
        state = self.root / 'state.json'
        self.module.write_state(state, {'stage': 'pending'})
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
        with patch.object(self.module.os, 'replace', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError):
                self.module.write_state(state, {'stage': 'running'})
        self.assertEqual(json.loads(state.read_bytes()), {'stage': 'pending'})
        self.assertEqual(list(self.root.iterdir()), [state])
        self.module.write_state(state, {'stage': 'running'})
        self.assertEqual(json.loads(state.read_bytes()), {'stage': 'running'})

    def test_state_refuses_shared_directory(self):
        self.root.chmod(0o755)
        state = self.root / 'state.json'
        with self.assertRaisesRegex(ValueError, 'Unsafe batch state directory'):
            self.module.write_state(state, {})
        self.assertFalse(state.exists())

    def test_attempt_needs_durable_receipt_before_it_is_finished(self):
        project = self.root / 'repo'
        project.mkdir()
        attempt = self.module.Attempt('example', project, self.root / 'receipt.json')
        self.assertEqual(attempt.status(), ('not_started', None))
        attempt.claim()
        self.assertEqual(attempt.status(), ('unknown', None))
        with self.assertRaises(FileExistsError):
            attempt.claim()
        result = self.module.RunResult('codex', 75, process_exit=75)
        with patch.object(self.module.os, 'link', side_effect=OSError('simulated publication failure')):
            with self.assertRaises(OSError):
                attempt.finish(result)
        self.assertEqual(attempt.status(), ('unknown', None))
        attempt.finish(result)
        self.assertEqual(attempt.status(), ('finished', result))
        with self.assertRaises(FileExistsError):
            attempt.finish(self.module.RunResult('busy', 75))
        self.assertEqual(attempt.status(), ('finished', result))

    def test_attempt_rejects_bad_receipt_identity_or_result(self):
        project = self.root / 'repo'
        project.mkdir()
        attempt = self.module.Attempt('example', project, self.root / 'receipt.json')
        attempt.claim()
        self.module.write_state(attempt.receipt, dict(attempt.identity, attempt_id='different',
                                                     result={'kind': 'codex', 'exit_code': 0}))
        with self.assertRaisesRegex(ValueError, 'identity'):
            attempt.status()
        self.module.write_state(attempt.receipt, dict(attempt.identity, result={
            'kind': 'codex', 'exit_code': 0, 'process_exit': 7,
        }))
        with self.assertRaisesRegex(ValueError, 'Invalid receipt result'):
            attempt.status()

    def test_attempt_with_uncertain_quiescence_stays_unknown(self):
        project = self.root / 'repo'
        project.mkdir()
        attempt = self.module.Attempt('uncertain', project, self.root / 'receipt.json')
        attempt.claim()
        result = self.module.RunResult('unknown', 76, process_exit=0, stage='quiescence')
        attempt.finish(result)
        self.assertEqual(attempt.status(), ('unknown', result))


if __name__ == '__main__':
    unittest.main()
