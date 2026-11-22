"""Manifest and durable JSON primitives. This module does not launch tasks."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Optional

from project_lock import UNKNOWN_EXIT, canonical_project


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate manifest key: {key}')
        result[key] = value
    return result


def parse_manifest(raw, *, base, snapshot_files=False):
    """Validate v1 and normalize every task to an inline prompt snapshot."""
    try:
        manifest = json.loads(raw, object_pairs_hook=unique_object)
    except RecursionError as error:
        raise ValueError('Manifest nesting is too deep') from error
    if not isinstance(manifest, dict) or set(manifest) != {'version', 'project', 'tasks'}:
        raise ValueError('Manifest must contain exactly version, project and tasks')
    if type(manifest['version']) is not int or manifest['version'] != 1:
        raise ValueError('Unsupported manifest version')
    project = manifest['project']
    if not isinstance(project, str) or not project or '\0' in project:
        raise ValueError('Manifest project must be a directory path')
    tasks = manifest['tasks']
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('Manifest tasks must be a nonempty array')
    ids = set()
    prompt_bytes = 0
    for task in tasks:
        if not isinstance(task, dict) or (
                set(task) != {'id', 'prompt'}
                and not (snapshot_files and set(task) == {'id', 'prompt_file'})):
            raise ValueError('Each task must contain id and exactly one supported prompt source')
        name = task['id']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
            raise ValueError('Invalid task id')
        if name in ids:
            raise ValueError(f'Duplicate task id: {name}')
        ids.add(name)
        if 'prompt_file' in task:
            filename = task.pop('prompt_file')
            if not isinstance(filename, str) or not filename or '\0' in filename:
                raise ValueError(f'Invalid prompt file for task: {name}')
            task['prompt'] = read_text(Path(base) / filename, limit=1024 * 1024)
        prompt = task['prompt']
        if not isinstance(prompt, str) or not prompt.strip() or '\0' in prompt:
            raise ValueError(f'Invalid prompt for task: {name}')
        prompt_bytes += len(prompt.encode('utf-8'))
        if prompt_bytes > 16 * 1024 * 1024:
            raise ValueError('Batch prompts exceed size limit')
    manifest['project'] = str(canonical_project(Path(base) / project))
    return manifest


def read_text(path, *, limit, private=False):
    flags = os.O_RDONLY | os.O_NONBLOCK
    if private:
        flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Expected a regular file')
        if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('Unsafe batch state file')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Batch input exceeds size limit')
    return data.decode('utf-8')


def write_state(path, state, *, replace=True):
    """Durably replace JSON in an existing private directory owned by this user."""
    payload = (json.dumps(state, allow_nan=False) + '\n').encode('utf-8')
    path = Path(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Unsafe batch state directory')
        fd, temporary = tempfile.mkstemp(prefix='.batch-', dir=f'/proc/self/fd/{directory}')
        name = os.path.basename(temporary)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            else:
                os.link(name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.fsync(directory)
    finally:
        os.close(directory)


@dataclass(frozen=True)
class RunResult:
    kind: str
    exit_code: int
    process_exit: Optional[int] = None
    signal: Optional[int] = None
    stage: Optional[str] = None
    capture_failed: bool = False


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    project: Path
    receipt: Path

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', self.attempt_id):
            raise ValueError('Invalid attempt id')
        if not self.receipt.is_absolute() or not self.receipt.name:
            raise ValueError('Receipt must be an absolute file path')
        directory = self.receipt.parent.resolve(strict=True)
        if directory == self.project or self.project in directory.parents:
            raise ValueError('Receipt must be outside the project')

    @property
    def identity(self):
        return {'version': 1, 'attempt_id': self.attempt_id, 'project': str(self.project)}

    @property
    def claim_path(self):
        return self.receipt.with_name(self.receipt.name + '.started')

    def claim(self):
        if os.path.lexists(self.receipt):
            raise FileExistsError(f'Receipt already exists: {self.receipt}')
        write_state(self.claim_path, self.identity, replace=False)

    def finish(self, result):
        if not valid_result(result) or self.read_record(self.claim_path) is None:
            raise ValueError('Cannot publish an invalid or unclaimed attempt result')
        write_state(self.receipt, dict(self.identity, result=asdict(result)), replace=False)

    def read_record(self, path):
        def opener(name, flags):
            return os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK)

        try:
            with open(path, 'rb', opener=opener) as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError('Unsafe attempt record')
                data = stream.read(4097)
        except FileNotFoundError:
            return None
        if len(data) > 4096:
            raise ValueError('Attempt record is too large')
        try:
            record = json.loads(data, object_pairs_hook=unique_object)
        except RecursionError as error:
            raise ValueError('Attempt record nesting is too deep') from error
        if (not isinstance(record, dict) or type(record.get('version')) is not int
                or any(record.get(k) != v for k, v in self.identity.items())):
            raise ValueError('Attempt record does not match its identity')
        return record

    def status(self):
        """Return (not_started, unknown, or finished) and a verified result, if present."""
        record = self.read_record(self.receipt)
        claim = self.read_record(self.claim_path)
        if record is None:
            return ('unknown' if claim is not None else 'not_started'), None
        if claim is None or not isinstance(record.get('result'), dict):
            raise ValueError('Receipt is missing its claim or result')
        try:
            result = RunResult(**record['result'])
        except TypeError as error:
            raise ValueError('Invalid receipt result') from error
        if not valid_result(result):
            raise ValueError('Invalid receipt result')
        return ('unknown' if result.kind == 'unknown' else 'finished'), result


def valid_result(result):
    code, process, number = result.exit_code, result.process_exit, result.signal
    if (type(code) is not int or not 0 <= code <= 255
            or process is not None and (type(process) is not int or not -127 <= process <= 255)
            or type(result.capture_failed) is not bool):
        return False
    if result.kind == 'codex':
        return (process is not None and code == (128 - process if process < 0 else process)
                and number is None and result.stage is None)
    if result.kind == 'cancelled':
        return (type(number) is int and number in (1, 2, 15)
                and code == 128 + number and result.stage is None)
    if result.kind == 'busy':
        return code == 75 and process is None and number is None and result.stage is None
    if result.kind == 'unknown':
        return (code == UNKNOWN_EXIT and result.stage in ('admission', 'quiescence')
                and (number is None or type(number) is int and number in (1, 2, 15)))
    return (result.kind == 'infrastructure_error' and code != 0 and process is None
            and number is None and result.stage in ('launch', 'prepare', 'admission', 'handoff'))
