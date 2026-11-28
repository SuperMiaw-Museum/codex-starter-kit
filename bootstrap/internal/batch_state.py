"""Manifest and durable JSON primitives. This module does not launch tasks."""
from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Optional

import project_lock

try:
    from graphlib import TopologicalSorter
except ImportError:
    TopologicalSorter = None

MAX_STATE = 16 * 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate manifest key: {key}')
        result[key] = value
    return result


def read_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except RecursionError as error:
        raise ValueError('Batch JSON nesting is too deep') from error


def require_version(manifest, source):
    version = manifest.get('version') if isinstance(manifest, dict) else None
    if type(version) is not int or version != 2:
        raise ValueError(f'Unsupported batch {source} version {version!r}; only version 2 is supported')


def parse_manifest(raw, base):
    return normalize_manifest(read_json(raw), base)


def normalize_manifest(manifest, base, snapshot=False):
    require_version(manifest, 'manifest')
    if TopologicalSorter is None:
        raise ValueError('Batch requires Python 3.9 or newer with graphlib')
    if manifest.keys() - {'version', 'tasks', 'max_parallel'}:
        raise ValueError('Invalid batch manifest keys')
    parallel = manifest.get('max_parallel', 1)
    if type(parallel) is not int or parallel < 1:
        raise ValueError('max_parallel must be a positive integer')
    tasks = manifest.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('Manifest tasks must be a nonempty array')
    normalized = []
    graph = {}
    prompt_bytes = 0
    for task in tasks:
        if (not isinstance(task, dict) or not {'id', 'project'} <= task.keys()
                or task.keys() - {'id', 'project', 'prompt', 'prompt_file', 'depends_on'}):
            raise ValueError('Invalid batch task keys')
        name = task['id']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
            raise ValueError('Invalid task id')
        if name in graph:
            raise ValueError(f'Duplicate task id: {name}')
        project = task['project']
        if not isinstance(project, str) or not project or '\0' in project:
            raise ValueError('Manifest project must be a directory path')
        if snapshot:
            path = Path(project)
            if not path.is_absolute() or str(path) != project or '..' in path.parts:
                raise ValueError('Invalid snapshot project')
        else:
            project = str(project_lock.canonical_project(Path(base) / project))
        deps = task.get('depends_on', [])
        if (not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps)
                or len(set(deps)) != len(deps)):
            raise ValueError('depends_on must be a list of unique task IDs')
        prompt = task_prompt(task, base, snapshot)
        prompt_bytes += len(prompt.encode('utf-8'))
        if prompt_bytes > MAX_STATE:
            raise ValueError('Batch prompts exceed size limit')
        normalized.append(dict(id=name, project=project, prompt=prompt, depends_on=deps))
        graph[name] = deps
    for name, deps in graph.items():
        if not graph.keys() >= set(deps):
            raise ValueError(f'Unknown dependency for task {name}')
    # prepare validates cycles without traversing Python's call stack.
    TopologicalSorter(graph).prepare()
    return dict(version=2, max_parallel=parallel, tasks=normalized)


def task_prompt(task, base, snapshot):
    if 'prompt_file' in task and not snapshot and 'prompt' not in task:
        filename = task['prompt_file']
        if not isinstance(filename, str) or not filename or '\0' in filename:
            raise ValueError(f'Invalid prompt file for task: {task["id"]}')
        prompt = read_text(Path(base) / filename, limit=1024 * 1024)
    elif 'prompt' in task and 'prompt_file' not in task:
        prompt = task['prompt']
    else:
        raise ValueError('Each task must contain exactly one supported prompt source')
    if not isinstance(prompt, str) or not prompt.strip() or '\0' in prompt:
        raise ValueError(f'Invalid prompt for task: {task["id"]}')
    return prompt


def union_resources(resources):
    union = {}
    for group in resources.values():
        for path, record in group.items():
            if path in union and record['identity'] != union[path]['identity']:
                raise ValueError(f'Resource identity changed: {path}')
            if path not in union or record['mode'] == 'rw':
                union[path] = record
    project_lock.validate_roots(union)
    return union


def validate_state(directory, state):
    if not isinstance(state, dict):
        raise ValueError('Invalid batch state')
    require_version(state.get('manifest'), 'state')
    if (set(state) != {'batch_id', 'manifest', 'resources'} or state['batch_id'] != directory.name
            or normalize_manifest(state['manifest'], directory, snapshot=True) != state['manifest']):
        raise ValueError('Invalid batch snapshot')
    resources = state['resources']
    projects = {task['project'] for task in state['manifest']['tasks']}
    if not isinstance(resources, dict) or resources.keys() != projects:
        raise ValueError('Invalid resource snapshot')
    for project, group in resources.items():
        if not isinstance(group, dict) or project not in group:
            raise ValueError('Missing primary project resource')
        for name, record in group.items():
            path = Path(name)
            if (not path.is_absolute() or str(path) != name or '..' in path.parts or '\0' in name
                    or not isinstance(record, dict) or record.keys() != {'mode', 'identity'}
                    or record['mode'] not in ('rw', 'ro')
                    or (name == project and record['mode'] != 'rw')
                    or not project_lock.valid_identity(record['identity'])):
                raise ValueError('Invalid resource snapshot entry')
    union_resources(resources)
    return state


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


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_state(path, state):
    """Durably publish immutable JSON in an existing private directory owned by this user."""
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


SESSION_PATTERN = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    project: Path
    receipt: Path
    task_id: str

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
        return dict(version=2, attempt_id=self.attempt_id, project=str(self.project),
                    task_id=self.task_id)

    @property
    def claim_path(self):
        return self.receipt.with_name(self.receipt.name + '.started')

    def claim(self):
        if os.path.lexists(self.receipt):
            raise FileExistsError(f'Receipt already exists: {self.receipt}')
        write_state(self.claim_path, self.identity)

    def finish(self, result):
        if not valid_result(result) or self.read_record(self.claim_path) is None:
            raise ValueError('Cannot publish an invalid or unclaimed attempt result')
        write_state(self.receipt, dict(self.identity, result=asdict(result)))

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
        keys = set(self.identity)
        if path == self.receipt:
            keys.add('result')
        elif path == self.session_path:
            keys.add('session_id')
        if record.keys() != keys:
            raise ValueError('Invalid attempt record fields')
        return record

    @property
    def session_path(self):
        return self.claim_path.with_suffix('.session')

    def session(self):
        if self.read_record(self.claim_path) is None:
            return None
        record = self.read_record(self.session_path)
        value = record.get('session_id') if record else None
        return value if isinstance(value, str) and SESSION_PATTERN.fullmatch(value) else None

    def status(self):
        """Return (not_started, unknown, or finished) and a verified result, if present."""
        record = self.read_record(self.receipt)
        claim = self.read_record(self.claim_path)
        if record is None:
            return ('unknown' if claim is not None else 'not_started'), None
        if claim is None or not isinstance(record.get('result'), dict):
            raise ValueError('Receipt is missing its claim or result')
        if record['result'].keys() != {field.name for field in fields(RunResult)}:
            raise ValueError('Incomplete receipt result')
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
        return (code == project_lock.UNKNOWN_EXIT and result.stage in ('admission', 'quiescence')
                and (number is None or type(number) is int and number in (1, 2, 15)))
    return (result.kind == 'infrastructure_error' and code != 0 and process is None
            and number is None and result.stage in ('launch', 'prepare', 'admission', 'handoff'))
