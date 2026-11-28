"""Dependency scheduling and process supervision for batches."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import fcntl
import heapq
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from batch_state import Attempt, TopologicalSorter, sync_directory, union_resources
import project_lock
import run_codex

INTERVAL = 0.05
HOST_CAP = 'CODEX_STARTER_KIT_BATCH_HOST_MAX_PARALLEL'


def snapshot_resources(manifest, check_project):
    resources = {}
    for task in manifest['tasks']:
        project = task['project']
        if project not in resources:
            resources[project] = project_lock.task_resources(project)
            for path in resources[project]:
                check_project(Path(path))
    union_resources(resources)
    return resources


def attempt_for(directory, state, rank):
    task = state['manifest']['tasks'][rank]
    attempt_id = f"{state['batch_id']}-{rank + 1}"
    return Attempt(attempt_id, Path(task['project']), directory / f'{attempt_id}.json', task['id'])


def task_row(directory, state, rank):
    task = state['manifest']['tasks'][rank]
    attempt = attempt_for(directory, state, rank)
    row = {key: task[key] for key in ('id', 'project', 'depends_on')}
    try:
        outcome, result = attempt.status()
    except (OSError, ValueError):
        outcome, result = 'unknown', None
    if outcome == 'not_started':
        row['status'] = ('unknown' if os.path.lexists(attempt.session_path)
                         else 'pending')
    else:
        row['status'] = outcome
    if outcome == 'finished':
        row['status'] = 'succeeded' if result.kind == 'codex' and result.exit_code == 0 else 'failed'
    if row['status'] != 'pending':
        row['attempt_id'] = attempt.attempt_id
        if result is not None:
            row['result'] = asdict(result)
        try:
            session = attempt.session()
        except (OSError, ValueError):
            session = None
        if session:
            row['session_id'] = session
    return row


def rows_for(directory, state, *, verify_dependencies=False):
    rows = [task_row(directory, state, rank) for rank in range(len(state['manifest']['tasks']))]
    if not verify_dependencies:
        return rows
    succeeded = {row['id'] for row in rows if row['status'] == 'succeeded'}
    for row in rows:
        if row['status'] in ('succeeded', 'failed') and not succeeded.issuperset(row['depends_on']):
            # A conclusive attempt cannot have started before its dependencies succeeded.
            row['status'] = 'unknown'
            row.pop('result', None)
    return rows


def show_status(directory, state):
    rows = rows_for(directory, state)
    statuses = {row['status'] for row in rows}
    status = next(value for value in ('unknown', 'failed', 'pending', 'succeeded') if value in statuses)
    print(json.dumps(dict(version=2, batch_id=state['batch_id'], status=status, tasks=rows)))
    return 0


def refusal(rows):
    for outcome in ('unknown', 'failed'):
        for row in rows:
            if row['status'] == outcome:
                if outcome == 'unknown':
                    print(f'UNKNOWN: attempt {row["attempt_id"]} has no conclusive receipt', file=sys.stderr)
                    return project_lock.UNKNOWN_EXIT
                code = row['result']['exit_code']
                print(f'Batch stopped at {row["id"]}: {row["result"]["kind"]} ({code})', file=sys.stderr)
                return code
    return 0


def host_cap():
    value = os.environ.get(HOST_CAP, '1')
    if not re.fullmatch(r'[1-9][0-9]{0,8}', value):
        raise ValueError(f'{HOST_CAP} must be an integer from 1 to 999999999')
    return int(value)


@contextmanager
def host_policy(directory, cap):
    # Keep the persistent coordination names shared with existing v2 processes.
    policy = f'v2-cap-{cap}'
    with ExitStack() as held:
        with project_lock.exclusive(directory, 'resource-admission'):
            for name in project_lock.names_in(directory):
                if name.startswith('v2-cap-') and name != policy:
                    with project_lock.exclusive(directory, name):
                        pass
            fd = held.enter_context(project_lock.flock_file(directory, policy, fcntl.LOCK_SH))
        yield fd


def conflicts(resources, active):
    return any(resource in other and (info['mode'] == 'rw' or other[resource]['mode'] == 'rw')
               for other in active for resource, info in resources.items())


def schedule(directory, state, batch_fd, resource_fds, union, cap, policy_fd, lock_dir, cancelled):
    tasks = state['manifest']['tasks']
    ranks = {task['id']: rank for rank, task in enumerate(tasks)}
    graph = TopologicalSorter({task['id']: task['depends_on'] for task in tasks})
    graph.prepare()
    rows = rows_for(directory, state, verify_dependencies=True)
    failure = refusal(rows)
    if failure:
        return failure
    ready, active = [], {}
    limit = min(cap, state['manifest']['max_parallel'], len(tasks))

    def collect_ready():
        while names := graph.get_ready():
            for name in names:
                rank = ranks[name]
                if rows[rank]['status'] == 'succeeded':
                    graph.done(name)
                else:
                    heapq.heappush(ready, rank)

    def observe():
        nonlocal failure
        completed = False
        for rank, (child, lease, _, _) in list(active.items()):
            if child.poll() is None:
                continue
            completed = True
            del active[rank]
            lease.__exit__(None, None, None)
            try:
                # A killed worker may have linked a receipt before syncing the directory.
                sync_directory(directory)
                row = task_row(directory, state, rank)
            except OSError as error:
                print(f'UNKNOWN: could not sync receipt for {tasks[rank]["id"]}: {error}',
                      file=sys.stderr)
                if not failure:
                    failure = project_lock.UNKNOWN_EXIT
                continue
            rows[rank] = row
            if row['status'] == 'succeeded':
                graph.done(row['id'])
            else:
                if row['status'] == 'pending':
                    row.update(status='unknown', attempt_id=attempt_for(directory, state, rank).attempt_id)
                code = refusal([row])
                if not failure:
                    failure = code
        return completed

    def slot():
        # At most one lease per active task. Start at the lowest slot to reuse files.
        busy = {entry[3] for entry in active.values()}
        for index in range(cap):
            if index in busy:
                continue
            lease = project_lock.exclusive(lock_dir, f'v2-slot-{index}')
            try:
                return index, lease, lease.__enter__()
            except project_lock.ProjectBusy:
                continue
        return None

    signal_sent = False

    def forward_signal():
        nonlocal signal_sent
        if cancelled() and not signal_sent:
            for child, _, _, _ in active.values():
                child.send_signal(cancelled())
            signal_sent = True

    try:
        while True:
            observe()
            forward_signal()
            if not failure and not cancelled():
                collect_ready()
                blocked = []
                while ready and len(active) < limit and not failure and not cancelled():
                    rank = heapq.heappop(ready)
                    task = tasks[rank]
                    resources = state['resources'][task['project']]
                    if conflicts(resources, (entry[2] for entry in active.values())):
                        blocked.append(rank)
                        continue
                    attempt = attempt_for(directory, state, rank)
                    acquired = slot()
                    if acquired is None:
                        heapq.heappush(ready, rank)
                        break
                    index, lease, slot_fd = acquired
                    started = False
                    try:
                        # A completed child must be observed before another admission.
                        if observe():
                            heapq.heappush(ready, rank)
                            break
                        if cancelled():
                            break
                        attempt.claim()
                        args = [sys.executable, '-B', str(Path(__file__).with_name('batch.py')),
                                '--v2-worker', str(directory), str(rank), str(cap), str(index),
                                str(batch_fd), str(policy_fd), str(slot_fd),
                                *(str(resource_fds[name]) for name in sorted(union))]
                        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, start_new_session=True,
                                                 pass_fds=(batch_fd, policy_fd, slot_fd,
                                                           *resource_fds.values()))
                        active[rank] = child, lease, resources, index
                        started = True
                    except (OSError, ValueError) as error:
                        print(f'UNKNOWN: could not launch attempt {attempt.attempt_id}: {error}',
                              file=sys.stderr)
                        failure = project_lock.UNKNOWN_EXIT
                    finally:
                        if not started:
                            lease.__exit__(None, None, None)
                for rank in blocked:
                    heapq.heappush(ready, rank)
            if not active and (failure or cancelled() or not graph.is_active()):
                return failure or (128 + cancelled() if cancelled() else 0)
            if active or ready:
                time.sleep(INTERVAL)
            else:
                raise RuntimeError('No schedulable tasks in validated graph')
    finally:
        # An infrastructure exception still leaves admitted workers responsible
        # for their descendants. Keep their reservations while draining.
        while active:
            forward_signal()
            for rank, (child, lease, _, _) in list(active.items()):
                if child.poll() is not None:
                    lease.__exit__(None, None, None)
                    del active[rank]
            if active:
                time.sleep(INTERVAL)


def worker(directory, state, rank, cap, index, batch_fd, policy_fd, slot_fd, fds, cancelled):
    union = union_resources(state['resources'])
    if len(fds) != len(union) or not 0 <= index < cap:
        raise ValueError('Invalid batch worker reservation')
    with project_lock.lock_directory() as lock_dir:
        project_lock.check_reservation(policy_fd, lock_dir, f'v2-cap-{cap}', writable=False)
        project_lock.check_reservation(slot_fd, lock_dir, f'v2-slot-{index}')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        project_lock.check_reservation(batch_fd, fd, 'supervisor')
    finally:
        os.close(fd)
    task = state['manifest']['tasks'][rank]
    attempt = attempt_for(directory, state, rank)
    if attempt.status()[0] != 'unknown' or os.path.lexists(attempt.receipt):
        raise ValueError('Worker requires an unfinished claimed attempt')
    prompt = task['prompt'].encode('utf-8')
    inputs = run_codex.task_from_prompt(attempt.project, None, prompt, prompt)
    result = run_codex.run_task(
        inputs, None, False, cancelled, cooperative_batch=True,
        resources=state['resources'][task['project']], resource_fds=dict(zip(sorted(union), fds)),
        reserved_modes=union, session_record=attempt.claim_path)
    attempt.finish(result)
    return result.exit_code
