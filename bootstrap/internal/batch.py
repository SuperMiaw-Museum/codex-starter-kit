"""Dependency-based execution for cooperating direct runners on one Linux host."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from batch_state import (
    MAX_STATE, parse_manifest, read_json, read_text, sync_directory, union_resources,
    validate_state, write_state,
)
import batch_scheduler
import project_lock
import run_codex


STATE_ROOT = Path.home() / '.codex-starter-kit-batches'
ID_PATTERN = r'[a-f0-9]{32}'


def private_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('Unsafe batch state directory')


def batch_directory(batch_id):
    if not re.fullmatch(ID_PATTERN, batch_id):
        raise ValueError('Invalid batch ID')
    private_directory(STATE_ROOT)
    directory = STATE_ROOT / batch_id
    private_directory(directory)
    return directory


def check_project(project):
    root = STATE_ROOT.parent.resolve(strict=True) / STATE_ROOT.name
    if project == root or project in root.parents or root in project.parents:
        raise ValueError('Project overlaps the batch state directory')


def create_batch(manifest, resources):
    batch_id = uuid.uuid4().hex
    try:
        STATE_ROOT.mkdir(mode=0o700)
    except FileExistsError:
        pass
    private_directory(STATE_ROOT)
    sync_directory(STATE_ROOT.parent)
    directory = STATE_ROOT / batch_id
    directory.mkdir(mode=0o700)
    state = dict(batch_id=batch_id, manifest=manifest, resources=resources)
    write_state(directory / 'state.json', state)
    sync_directory(STATE_ROOT)
    return directory, state


def load_batch(directory):
    raw = read_text(directory / 'state.json', limit=MAX_STATE, private=True)
    return validate_state(directory, read_json(raw))


@contextmanager
def batch_supervisor(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with project_lock.exclusive(fd, 'supervisor') as batch_fd:
            yield batch_fd
    finally:
        os.close(fd)


def execute_batch(directory, manifest, cancelled):
    cap = batch_scheduler.host_cap()
    if directory is None:
        resources = batch_scheduler.snapshot_resources(manifest, check_project)
        initial = dict(batch_id='0' * 32, manifest=manifest, resources=resources)
        if len((json.dumps(initial) + '\n').encode('utf-8')) > MAX_STATE:
            raise ValueError('Batch snapshot exceeds size limit')
        union = union_resources(resources)
        with project_lock.lock_directory() as lock_dir, batch_scheduler.host_policy(lock_dir, cap) as policy:
            with project_lock.reserve_resources(union) as reservations:
                if cancelled():
                    return 128 + cancelled()
                directory, state = create_batch(manifest, resources)
                with batch_supervisor(directory) as batch_fd:
                    print(f'BATCH_ID={directory.name}', flush=True)
                    return batch_scheduler.schedule(
                        directory, state, batch_fd, reservations, union, cap, policy, lock_dir, cancelled)
    with batch_supervisor(directory) as batch_fd:
        sync_directory(directory)
        state = load_batch(directory)
        rows = batch_scheduler.rows_for(directory, state, verify_dependencies=True)
        failure = batch_scheduler.refusal(rows)
        if failure:
            return failure
        if all(row['status'] == 'succeeded' for row in rows):
            return 128 + cancelled() if cancelled() else 0
        manifest = state['manifest']
        resources = batch_scheduler.snapshot_resources(manifest, check_project)
        if resources != state['resources']:
            raise ValueError('Repository resources changed since the batch snapshot')
        union = union_resources(resources)
        with project_lock.lock_directory() as lock_dir, batch_scheduler.host_policy(lock_dir, cap) as policy:
            with project_lock.reserve_resources(union) as reservations:
                return batch_scheduler.schedule(
                    directory, state, batch_fd, reservations, union, cap, policy, lock_dir, cancelled)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run a cooperative Codex batch')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run').add_argument('manifest', type=Path)
    for command in ('status', 'resume'):
        commands.add_parser(command).add_argument('id')
    args = parser.parse_args(argv)
    target = str(args.manifest if args.command == 'run' else args.id)
    try:
        if args.command == 'run':
            raw = read_text(args.manifest, limit=MAX_STATE)
            manifest = parse_manifest(raw, args.manifest.absolute().parent)
            with run_codex.handled_signals() as cancelled:
                return execute_batch(None, manifest, cancelled)
        directory = batch_directory(args.id)
        if args.command == 'status':
            return batch_scheduler.show_status(directory, load_batch(directory))
        load_batch(directory)
        with run_codex.handled_signals() as cancelled:
            return execute_batch(directory, None, cancelled)
    except project_lock.ProjectBusy:
        print(f'BUSY: batch or resource is already reserved: {target}', file=sys.stderr)
        return project_lock.BUSY_EXIT
    except project_lock.ProjectUnknown:
        print(f'UNKNOWN: resource fenced after unconfirmed execution: {target}', file=sys.stderr)
        return project_lock.UNKNOWN_EXIT
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Batch: {error}', file=sys.stderr)
        return 2


def worker_main(argv):
    try:
        directory = batch_directory(Path(argv[0]).name)
        if str(directory) != argv[0]:
            raise ValueError('Invalid worker batch directory')
        rank, cap, index, batch_fd, policy_fd, slot_fd, *fds = map(int, argv[1:])
        with run_codex.handled_signals() as cancelled:
            state = load_batch(directory)
            if not 0 <= rank < len(state['manifest']['tasks']):
                raise ValueError('Invalid worker task rank')
            return batch_scheduler.worker(directory, state, rank, cap, index, batch_fd, policy_fd,
                                          slot_fd, fds, cancelled)
    except (OSError, ValueError, RuntimeError, IndexError, KeyError) as error:
        print(f'Batch worker: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    if sys.argv[1:2] == ['--v2-worker']:
        sys.exit(worker_main(sys.argv[2:]))
    sys.exit(main())
