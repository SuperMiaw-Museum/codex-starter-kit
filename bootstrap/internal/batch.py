"""Sequential execution for cooperating direct runners on one Linux host."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from batch_state import Attempt, parse_manifest, read_text, unique_object, write_state
import project_lock
import run_codex


STATE_ROOT = Path.home() / '.codex-starter-kit-batches'
MAX_STATE = 16 * 1024 * 1024
ID_PATTERN = r'[a-f0-9]{32}'


def private_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('Unsafe batch state directory')


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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


def create_batch(manifest):
    batch_id = uuid.uuid4().hex
    try:
        STATE_ROOT.mkdir(mode=0o700)
    except FileExistsError:
        pass
    private_directory(STATE_ROOT)
    sync_directory(STATE_ROOT.parent)
    directory = STATE_ROOT / batch_id
    directory.mkdir(mode=0o700)
    state = {'batch_id': batch_id, 'manifest': manifest, 'started': 0}
    write_state(directory / 'state.json', state, replace=False)
    sync_directory(STATE_ROOT)
    return directory, state


def load_batch(directory):
    raw = read_text(directory / 'state.json', limit=MAX_STATE, private=True)
    state = json.loads(raw, object_pairs_hook=unique_object)
    if (not isinstance(state, dict) or state.keys() != {'batch_id', 'manifest', 'started'}
            or state['batch_id'] != directory.name or type(state['started']) is not int):
        raise ValueError('Invalid batch state')
    manifest = state['manifest']
    validated = parse_manifest(json.dumps(manifest), base=directory)
    if validated != manifest or not 0 <= state['started'] <= len(validated['tasks']):
        raise ValueError('Invalid batch snapshot or project no longer canonical')
    return state


def task_attempt(directory, state, index):
    attempt_id = f"{state['batch_id']}-{index + 1}"
    return Attempt(attempt_id, Path(state['manifest']['project']), directory / f'{attempt_id}.json')


def receipt_status(attempt):
    try:
        status, result = attempt.status()
    except (OSError, ValueError):
        return 'unknown', None
    if status != 'finished':
        return 'unknown', result
    if result.kind == 'codex' and result.exit_code == 0:
        return 'succeeded', result
    return 'failed', result


def show_status(directory, state):
    rows = []
    for index, task in enumerate(state['manifest']['tasks']):
        row = {'id': task['id'], 'status': 'pending'}
        if index < state['started']:
            attempt = task_attempt(directory, state, index)
            status, result = receipt_status(attempt)
            row.update(status=status, attempt_id=attempt.attempt_id)
            if result is not None:
                row['result'] = asdict(result)
        rows.append(row)
    status = 'succeeded'
    for row in rows:
        if row['status'] != 'succeeded':
            status = row['status']
            break
    print(json.dumps({'version': 1, 'batch_id': state['batch_id'],
                      'project': state['manifest']['project'], 'status': status, 'tasks': rows}))
    return 0


def execute_batch(directory, state, reservation, cancelled):
    project = Path(state['manifest']['project'])
    for index, task in enumerate(state['manifest']['tasks']):
        if cancelled():
            return 128 + cancelled()
        attempt = task_attempt(directory, state, index)
        if index == state['started']:
            prompt = task['prompt'].encode('utf-8')
            inputs = run_codex.task_from_prompt(project, None, prompt, prompt)
            state['started'] += 1
            write_state(directory / 'state.json', state)
            try:
                attempt.claim()
                result = run_codex.run_task(inputs, reservation, False, cancelled,
                                            cooperative_batch=True)
                attempt.finish(result)
            except (OSError, ValueError) as error:
                print(f'UNKNOWN: could not complete attempt {attempt.attempt_id}: {error}',
                      file=sys.stderr)
                return project_lock.UNKNOWN_EXIT
        status, result = receipt_status(attempt)
        if status == 'unknown':
            print(f'UNKNOWN: attempt {attempt.attempt_id} has no conclusive receipt',
                  file=sys.stderr)
            return project_lock.UNKNOWN_EXIT
        if status == 'failed':
            print(f'Batch stopped at {task["id"]}: {result.kind} ({result.exit_code})',
                  file=sys.stderr)
            return result.exit_code
    return 128 + cancelled() if cancelled() else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run a sequential cooperative Codex batch')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run').add_argument('manifest', type=Path)
    for command in ('status', 'resume'):
        commands.add_parser(command).add_argument('id')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            manifest = parse_manifest(read_text(args.manifest, limit=MAX_STATE),
                                      base=args.manifest.absolute().parent, snapshot_files=True)
            # Bound the persisted form as well, including JSON escaping and snapshots.
            if len(json.dumps({'batch_id': '0' * 32, 'manifest': manifest,
                               'started': len(manifest['tasks'])}).encode('utf-8')) >= MAX_STATE:
                raise ValueError('Batch snapshot exceeds size limit')
            project = Path(manifest['project'])
        else:
            directory = batch_directory(args.id)
            state = load_batch(directory)
            if args.command == 'status':
                return show_status(directory, state)
            project = Path(state['manifest']['project'])
        check_project(project)
        with run_codex.handled_signals() as cancelled:
            with project_lock.reserve_project(project) as reservation:
                if cancelled():
                    return 128 + cancelled()
                if args.command == 'run':
                    directory, state = create_batch(manifest)
                    print(f'BATCH_ID={directory.name}', flush=True)
                else:
                    # Another resume could have advanced after the initial read.
                    state = load_batch(directory)
                    if state['manifest']['project'] != str(project):
                        raise ValueError('Batch project changed during admission')
                    sync_directory(directory)
                return execute_batch(directory, state, reservation, cancelled)
    except project_lock.ProjectBusy:
        print(f'BUSY: project is already reserved or running: {project}', file=sys.stderr)
        return project_lock.BUSY_EXIT
    except project_lock.ProjectUnknown:
        print(f'UNKNOWN: project fenced after unconfirmed execution: {project}', file=sys.stderr)
        return project_lock.UNKNOWN_EXIT
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Batch: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
