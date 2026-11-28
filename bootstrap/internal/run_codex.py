"""Supervise a Codex handoff and optionally display it in an isolated tmux pane."""
from contextlib import contextmanager
import ctypes
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from batch_state import Attempt, RunResult
from bubblewrap_exec import sealed_input
from codex_notice import terminal_text
import project_lock
import task_registry


INTERNAL = Path(__file__).resolve().parent
POLL_INTERVAL = 0.03
SIGNAL_GRACE = 0.75
CAPTURE_GRACE = 2
DESCENDANT_GRACE = 3
HANDLED_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
USAGE = 'Usage: run-codex.sh PROJECT_DIR [--resume SESSION_ID] (TASK_FILE | --task-file TASK_FILE | --task "instructions")'


def task_input(args):
    if not args:
        raise ValueError(USAGE)
    project, *args = args
    resume = None
    if args[:1] == ['--resume']:
        if len(args) < 3:
            raise ValueError(USAGE)
        resume = args[1]
        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', resume):
            raise ValueError('Session ID must be a UUID')
        args = args[2:]
    if len(args) == 2 and args[0] == '--task':
        prompt = os.fsencode(args[1])
        body = prompt
    elif ((len(args) == 2 and args[0] == '--task-file')
          or (len(args) == 1 and args[0] not in ('--task', '--task-file', '--resume'))):
        try:
            fd = os.open(args[-1], os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as task:
                if not stat.S_ISREG(os.fstat(task.fileno()).st_mode):
                    raise ValueError('Task file must be readable')
                prompt = task.read().replace(b'\0', b'')
        except OSError as error:
            raise ValueError('Task file must be readable') from error
        body = prompt.rstrip(b'\n')
    else:
        raise ValueError(USAGE)
    if not body:
        raise ValueError('Task must not be empty')
    project = project_lock.canonical_project(project or '.')
    return task_from_prompt(project, resume, body, prompt)


def task_from_prompt(project, resume, body, prompt):
    full = os.environ.get('CODEX_STARTER_KIT_SHOW_FULL_PROMPT') == '1'
    # Only the full-prompt option needs more than the preview and truncation sentinel.
    label = os.fsdecode(prompt if full else body[:4 * (task_registry.LABEL_LIMIT + 1)])
    return project, resume, body, label, full


@contextmanager
def handled_signals():
    received = []

    def remember(number, _frame):
        if not received:
            received.append(number)

    previous = {number: signal.signal(number, remember) for number in HANDLED_SIGNALS}
    try:
        yield lambda: received[0] if received else 0
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def send_group(pid, number):
    try:
        os.killpg(pid, number)
    except ProcessLookupError:
        return False
    return True


def enable_subreaper():
    # Linux PR_SET_CHILD_SUBREAPER. Orphans, including setsid descendants, are
    # adopted here instead of escaping observation when their parents exit.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)


def descendants_quiescent():
    deadline = time.monotonic() + DESCENDANT_GRACE
    final_reaps = 256
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        remaining = deadline - time.monotonic()
        if not pid:
            if remaining <= 0:
                return False
            time.sleep(min(POLL_INTERVAL, remaining))
        elif remaining <= 0:
            # Drain children already waitable at the deadline, without allowing
            # an unlimited stream of newly orphaned zombies to extend the wait.
            final_reaps -= 1
            if not final_reaps:
                return False


def child_snapshot():
    """Diagnostics only. Procfs observations never authorize fence removal."""
    parent = os.getpid()
    observed = []
    try:
        children = Path(f'/proc/self/task/{parent}/children').read_text().split()
    except OSError:
        return observed
    for pid in children[:16]:
        try:
            line = Path(f'/proc/{int(pid)}/stat').read_text()
            name, fields = line.rsplit(')', 1)
            state, ppid, _, session, *_ = fields.split()
            if int(ppid) == parent:
                name = name.split('(', 1)[1].encode('unicode_escape').decode('ascii')
                observed.append(f'{pid}:{name} state={state} sid={session}')
        except (OSError, ValueError, IndexError):
            continue
    return observed


def probe(command, project, env, cancelled):
    try:
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(command, cwd=project, env=env, stdout=output,
                                     stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
            deadline = time.monotonic() + 3
            while child.poll() is None:
                if cancelled() or time.monotonic() >= deadline:
                    send_group(child.pid, signal.SIGKILL)
                    child.wait(timeout=SIGNAL_GRACE)
                    return None
                time.sleep(POLL_INTERVAL)
            if child.returncode == 0:
                output.seek(0)
                return output.read(128 * 1024)
    except OSError:
        return None


def codex_command(executable, project, resume, env, cancelled):
    command = [executable, 'exec']
    if resume:
        command.append('resume')
    help_args = [*command, '--help']
    if probe(['git', 'rev-parse', '--show-toplevel'], project, env, cancelled) is None:
        command.append('--skip-git-repo-check')
    capture = False
    if not cancelled() and env.get('CODEX_STARTER_KIT_NOTIFY_METADATA', '1') == '1':
        help_text = probe(help_args, project, env, cancelled) or b''
        capture = bool(re.search(rb'\s--json(?:\s|$)', help_text))
    if capture:
        command.append('--json')
    if resume:
        command.append(resume)
    return [*command, '-'], capture


def capture_error(reason):
    try:
        print(f'Codex output capture failed: {reason}', file=sys.stderr, flush=True)
    except OSError:
        pass


def cancelled_result(number, process_exit=None, *, capture_failed=False):
    return RunResult('cancelled', 128 + number, process_exit=process_exit, signal=number,
                     capture_failed=capture_failed)


def execute(command, project, env, task_file, metadata, cancelled, session_file=None,
            report_capture_error=capture_error, lock_fds=(), *, protected=False, session_record=None):
    """Run the mission, preserving Codex's status independently of JSON capture."""
    if protected and metadata is None:
        raise ValueError('Protected execution requires captured output')
    parser = None
    with (sealed_input(task_file) if protected else task_file.open('rb')) as stdin:
        if cancelled():
            return cancelled_result(cancelled())
        try:
            child = subprocess.Popen(command, cwd=project, env={} if protected else env, stdin=stdin,
                                     stdout=subprocess.PIPE if metadata else None,
                                     stderr=subprocess.PIPE if protected else None,
                                     start_new_session=True, pass_fds=() if protected else lock_fds)
        except OSError as error:
            print(f'Could not launch Codex: {error}', file=sys.stderr, flush=True)
            return RunResult('infrastructure_error',
                             127 if isinstance(error, FileNotFoundError) else 126, stage='launch')
    exit_deadline = None
    cancel_deadline = None
    cancel_complete = False
    drained = False
    stderr_drained = not protected
    capture_failed = False
    try:
        if protected:
            errors = select.poll()
            errors.register(child.stderr, select.POLLIN)
        if metadata:
            reader = select.poll()
            reader.register(child.stdout, select.POLLIN)
            parser_args = [sys.executable, '-B', str(INTERNAL / 'codex_notice.py'), str(metadata)]
            if session_file:
                parser_args.append(str(session_file))
            elif protected or session_record:
                parser_args.extend(['', '1' if protected else ''])
            if session_record:
                parser_args.append(str(session_record))
            try:
                parser = subprocess.Popen(parser_args, cwd=project, env=env, stdin=child.stdout)
            except OSError as error:
                report_capture_error(error)
                capture_failed = True
        while True:
            now = time.monotonic()
            # Keep the session leader unreaped until all group signals are sent.
            # Its PID cannot be reused while it is still our waitable child.
            exited = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            number = cancelled()
            if number and cancel_deadline is None:
                cancel_deadline = now + SIGNAL_GRACE
                cancel_complete = not send_group(child.pid, number)
            if cancel_deadline is not None and not cancel_complete:
                if now >= cancel_deadline:
                    send_group(child.pid, signal.SIGKILL)
                    cancel_complete = True
            if cancel_complete and not exited and now >= cancel_deadline + SIGNAL_GRACE:
                raise subprocess.TimeoutExpired(command, 2 * SIGNAL_GRACE)
            if exited and exit_deadline is None:
                exit_deadline = now + CAPTURE_GRACE

            parser_code = parser.poll() if parser else None
            if parser_code:
                if not capture_failed:
                    report_capture_error(f'parser exited with status {parser_code}')
                capture_failed = True
            readable = False
            if not stderr_drained and errors.poll(0):
                readable = True
                chunk = os.read(child.stderr.fileno(), 65536)
                stderr_drained = not chunk
                if chunk:
                    try:
                        print(terminal_text(chunk.decode('utf-8', errors='backslashreplace')),
                              file=sys.stderr, end='', flush=True)
                    except OSError:
                        pass
            if (capture_failed or parser_code is not None) and not drained and reader.poll(0):
                # Keep a reader on Codex stdout if the parser stops early.
                readable = True
                drained = not os.read(child.stdout.fileno(), 65536)
                if not drained and not capture_failed:
                    report_capture_error('parser exited before consuming Codex output')
                    capture_failed = True
            capture_done = not metadata or drained
            if exited and (cancel_deadline is None or cancel_complete):
                if capture_done and stderr_drained:
                    break
                if now >= exit_deadline:
                    if not capture_failed:
                        report_capture_error('output did not close after Codex exited')
                    capture_failed = True
                    break
            if not readable:
                time.sleep(POLL_INTERVAL)
        code = child.wait()
        if number:
            return cancelled_result(number, code, capture_failed=capture_failed)
        return RunResult('codex', 128 - code if code < 0 else code, process_exit=code,
                         capture_failed=capture_failed)
    finally:
        if child.returncode is None:
            send_group(child.pid, signal.SIGKILL)
            try:
                child.wait(timeout=SIGNAL_GRACE)
            except subprocess.TimeoutExpired:
                # The final quiescence check retains the fence.
                pass
        if child.stdout is not None:
            child.stdout.close()
        if child.stderr is not None:
            child.stderr.close()
        if parser is not None:
            if parser.poll() is None:
                parser.kill()
            try:
                parser.wait(timeout=SIGNAL_GRACE)
            except subprocess.TimeoutExpired:
                # In direct mode the final tree check will retain the fence.
                pass


def pane_worker(directory):
    """Run inside tmux; the outer supervisor owns registry transitions."""
    command, project, capture = json.loads((directory / 'launch.json').read_text())
    enable_subreaper()
    with handled_signals() as received:
        def cancelled():
            if received():
                return received()
            try:
                return int((directory / 'cancel').read_text())
            except (FileNotFoundError, ValueError):
                return 0

        def report_capture_error(reason):
            try:
                (directory / 'capture-error').write_text(str(reason))
            except OSError:
                pass
            capture_error(reason)

        try:
            code = execute(command, project, os.environ, directory / 'task',
                           directory / 'metadata' if capture else None,
                           cancelled, directory / 'session-id', report_capture_error).exit_code
        except OSError as error:
            print(f'Could not launch Codex: {error}', file=sys.stderr, flush=True)
            code = 127
        if descendants_quiescent():
            (directory / 'quiescent').touch()
        else:
            code = project_lock.UNKNOWN_EXIT
        result = directory / 'status.tmp'
        result.write_text(f'{code}\n')
        result.replace(directory / 'status')
    return code


def inspect(tmux, directory, name, project, command, capture, env, cancelled):
    socket = directory / 'socket'
    tmux = [tmux, '-S', str(socket)]
    (directory / 'launch.json').write_text(json.dumps([command, str(project), capture]))
    try:
        launched = subprocess.run(
            [*tmux, '-f', '/dev/null', 'new-session', '-d', '-s', name, '-x', '120', '-y', '40',
             sys.executable, '-B', str(Path(__file__).resolve()), '--tmux-worker', str(directory)],
            env=env, timeout=4)
        if launched.returncode:
            print('Could not start the tmux inspection session', file=sys.stderr)
            return 1
        print(f'SANDBOX_HANDOFF_TMUX_SESSION={name}', flush=True)
        print(f'Read-only inspection: {attachment(directory, name)}', flush=True)
        deadline = None
        while True:
            number = cancelled()
            if number and deadline is None:
                (directory / 'cancel').write_text(str(number))
                deadline = time.monotonic() + SIGNAL_GRACE + CAPTURE_GRACE + 1
            if deadline is not None and time.monotonic() >= deadline:
                return 128 + number
            if (directory / 'status').exists():
                error = directory / 'capture-error'
                if error.is_file():
                    capture_error(error.read_text())
                marker = directory / 'session-id'
                if marker.is_file():
                    print(marker.read_text(), end='', flush=True)
                return 128 + number if number else int((directory / 'status').read_text())
            active = subprocess.run([*tmux, 'has-session', '-t', f'={name}'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            # Check again after has-session: a short mission may have just published.
            if active.returncode and not (directory / 'status').exists():
                print('The tmux inspection session ended before reporting a Codex exit code', file=sys.stderr)
                return 128 + number if number else 1
            time.sleep(0.15)
    finally:
        try:
            subprocess.run([*tmux, 'kill-server'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def attachment(directory, name):
    return shlex.join(['tmux', '-S', str(directory / 'socket'), 'attach', '-r', '-t', name])


def supervise(project, resume, body, label, full, executable, tmux, cancelled, lock_fds,
              protected_command=None, *, resources, session_record=None):
    env = dict(os.environ)
    env['PATH'] = str(Path(executable).parent) + os.pathsep + env.get('PATH', os.defpath)
    registered_token = None
    result = RunResult('infrastructure_error', 1, stage='prepare')
    metadata = None
    capture_setup_failed = False
    fences = project_lock.ExecutionFences()
    fenced = False
    tmux_started = False

    def notify(message, phase, code, *, token=None, **details):
        nonlocal registered_token
        if token is not None:
            registered_token = token
        task_registry.deliver(message, phase, code,
                              sys.stderr if phase == 'START' else sys.stdout, **details)

    prefix = 'codex-inspect.' if tmux else 'codex-handoff.'
    with tempfile.TemporaryDirectory(prefix=prefix, dir='/tmp') as workdir:
        directory = Path(workdir)
        name = directory.name.replace('.', '-', 1)
        try:
            task_file = directory / 'task'
            with os.fdopen(os.open(task_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(body)
                stream.write(b'\n')
            if cancelled():
                result = cancelled_result(cancelled())
                return result
            try:
                task_registry.start(
                    project.name or '/', label, pid=os.getpid(),
                    resume=resume.lower() if resume else None,
                    show_full_prompt=full and not protected_command,
                    inspect_command=attachment(directory, name) if tmux else '', notify=notify)
            except (OSError, ValueError, TypeError, KeyError) as error:
                print(f'Task registry: {error}', file=sys.stderr)
            if not cancelled():
                fenced = True
                fences.arm(resources)
                command, capture = ((protected_command, True) if protected_command
                                    else codex_command(executable, project, resume, env, cancelled))
                metadata = directory / 'metadata' if capture else None
                if metadata:
                    try:
                        metadata.touch(mode=0o600, exist_ok=False)
                    except OSError as error:
                        if protected_command:
                            raise
                        capture_error(error)
                        command.remove('--json')
                        metadata = None
                        capture = False
                        capture_setup_failed = True
                if not cancelled():
                    if tmux:
                        tmux_started = True
                        code = inspect(tmux, directory, name, project, command, capture, env, cancelled)
                        result = RunResult('tmux', code)
                    else:
                        result = execute(command, project, env, task_file, metadata, cancelled,
                                         lock_fds=lock_fds, protected=protected_command is not None,
                                         session_record=session_record)
            if cancelled():
                result = cancelled_result(cancelled(), result.process_exit, capture_failed=result.capture_failed)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f'Codex handoff: {error}', file=sys.stderr)
            result = (cancelled_result(cancelled()) if cancelled()
                      else RunResult('infrastructure_error', 1, stage='handoff'))
        finally:
            if fenced:
                try:
                    if (descendants_quiescent()
                            and (not tmux_started or (directory / 'quiescent').is_file())):
                        fences.clear()
                    else:
                        raise project_lock.ProjectUnknown
                except (OSError, project_lock.ProjectUnknown):
                    print('UNKNOWN: Codex process tree not confirmed clear; project fenced.',
                          file=sys.stderr, flush=True)
                    observed = child_snapshot()
                    if observed:
                        print('Observed children: ' + ', '.join(observed), file=sys.stderr, flush=True)
                    result = RunResult('unknown', project_lock.UNKNOWN_EXIT, process_exit=result.process_exit,
                                       signal=result.signal, stage='quiescence', capture_failed=result.capture_failed)
                finally:
                    fences.close()
            if registered_token:
                try:
                    task_registry.end(registered_token, result.exit_code, metadata=metadata, notify=notify,
                                      quota_enabled=env.get('CODEX_STARTER_KIT_LIMIT_REMINDER') == '1',
                                      codex_cwd=project)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    print(f'Task registry: {error}', file=sys.stderr)
            print(f'SANDBOX_HANDOFF_EXIT_CODE={result.exit_code}', flush=True)
    return replace(result, capture_failed=True) if capture_setup_failed else result


def run_task(task, reservation_fd, live, cancelled, isolation=None, *, cooperative_batch=False,
             resources=None, resource_fds=None, reserved_modes=None, session_record=None):
    if cancelled():
        return cancelled_result(cancelled())
    if cooperative_batch and ((reservation_fd is None and resource_fds is None)
                              or live or isolation is not None):
        raise ValueError('Cooperative batch requires a direct project reservation')
    if reservation_fd is not None and isolation is None and not cooperative_batch:
        try:
            project_lock.require_verified_execution_profile()
        except ValueError as error:
            print(error, file=sys.stderr)
            return RunResult('infrastructure_error', 2, stage='admission')
    executable = shutil.which('codex') if isolation is None else '/toolchain/bin/codex'
    if not executable:
        print('Codex not found in PATH', file=sys.stderr)
        return RunResult('infrastructure_error', 127, stage='launch')
    tmux = None
    if live:
        tmux = shutil.which('tmux')
        if not tmux:
            print('CODEX_STARTER_KIT_LIVE_INSPECT=1 requires tmux in PATH', file=sys.stderr)
            return RunResult('infrastructure_error', 127, stage='launch')
    try:
        current_resources = project_lock.task_resources(task[0])
        if resources is not None and current_resources != resources:
            raise ValueError('Repository resources changed since the batch snapshot')
        resources = current_resources
        with project_lock.project_execution(task[0], reservation_fd, resources=resources,
                                            resource_fds=resource_fds,
                                            reserved_modes=reserved_modes) as lock_fds:
            try:
                if project_lock.task_resources(task[0]) != resources:
                    raise ValueError('Repository resources changed during admission')
                enable_subreaper()
                protected_command = None
                if isolation is not None:
                    if live or reservation_fd is None:
                        raise ValueError('Offline isolation requires a direct reserved run')
                    isolation.validate(task[0])
                    protected_command = isolation.command(task[1])
                return supervise(*task, os.path.abspath(executable), tmux, cancelled,
                                 () if cooperative_batch else lock_fds,
                                 protected_command, resources=resources, session_record=session_record)
            except (OSError, ValueError) as error:
                print(f'Codex handoff: {error}', file=sys.stderr)
                return RunResult('infrastructure_error', 2, stage='handoff')
    except project_lock.ProjectBusy:
        print(f'BUSY: project is already reserved or running: {task[0]}', file=sys.stderr)
        return RunResult('busy', project_lock.BUSY_EXIT)
    except project_lock.ProjectUnknown:
        print(f'UNKNOWN: project fenced after unconfirmed execution: {task[0]}', file=sys.stderr)
        return RunResult('unknown', project_lock.UNKNOWN_EXIT, stage='admission')
    except (OSError, ValueError) as error:
        print(f'Project lock: {error}', file=sys.stderr)
        return RunResult('infrastructure_error', 2, stage='admission')


def main(args, *, offline_isolation_factory=None):
    reservation_fd = None
    request = None
    live = os.environ.get('CODEX_STARTER_KIT_LIVE_INSPECT') == '1'
    try:
        if args[:1] == ['--attempt-id']:
            if len(args) < 5 or args[2] != '--receipt':
                raise ValueError('Expected --attempt-id ID --receipt PATH')
            request = args[1], Path(args[3])
            args = args[4:]
        if args[:1] == [project_lock.RESERVATION_OPTION]:
            if (len(args) < 3 or not re.fullmatch(r'[0-9]{1,10}', args[1])
                    or not 3 <= int(args[1]) <= 2**31 - 1):
                raise ValueError('Invalid project reservation descriptor')
            reservation_fd = int(args[1])
            args = args[2:]
        if live and (reservation_fd is not None or request is not None):
            raise ValueError('Project reservations and attempt receipts require direct mode')
        if reservation_fd is not None or request is not None:
            if offline_isolation_factory is None:
                project_lock.require_verified_execution_profile()
        if offline_isolation_factory is not None and (reservation_fd is None or request is None):
            raise ValueError('Offline fixture requires a reservation and a receipt')
        task = task_input(args)
        attempt = Attempt(request[0], task[0], request[1], task_id=request[0]) if request else None
        isolation = None
        if offline_isolation_factory is not None:
            isolation = offline_isolation_factory(
                task[0], (project_lock.LOCK_ROOT, task_registry.ROOT, attempt.receipt.parent))
    except (OSError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 2

    with handled_signals() as cancelled:
        if attempt:
            try:
                attempt.claim()
            except (OSError, ValueError) as error:
                print(f'Attempt claim: {error}', file=sys.stderr)
                return 2
        result = run_task(task, reservation_fd, live, cancelled, isolation)
        if attempt:
            try:
                attempt.finish(result)
            except (OSError, ValueError) as error:
                print(f'Attempt receipt: {error}', file=sys.stderr)
                return 2
        return result.exit_code


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--tmux-worker':
        sys.exit(pane_worker(Path(sys.argv[2])))
    sys.exit(main(sys.argv[1:]))
