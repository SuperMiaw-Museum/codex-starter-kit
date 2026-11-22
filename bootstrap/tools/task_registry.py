"""Per-user task registry. All mutations and delivery share one advisory lock."""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time

# A fixed per-user path shares counts across checkouts and remote sessions.
ROOT = Path('/tmp') / f'codex-starter-kit-tasks-{os.getuid()}'
TTL = 24 * 60 * 60
LABEL_LIMIT = 120


def clean(value, limit):
    return ''.join(c if c.isprintable() else ' ' for c in value)[:limit]


def task_label(value):
    label = clean(value, LABEL_LIMIT)
    if len(value) > LABEL_LIMIT:
        label += '...'
    return label


def identity(pid):
    try:
        data = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if data[0] == 'Z':
            return None
        return [Path('/proc/sys/kernel/random/boot_id').read_text().strip(), data[19]]
    except (OSError, IndexError):
        return None


def stale(task):
    if task['mode'] == 'Codex':
        return identity(task['pid']) != task['identity']
    return time.time() - task['started'] >= TTL


def checked_open(directory, name, flags, *, create=False):
    flags |= os.O_NOFOLLOW | os.O_NONBLOCK
    if create:
        try:
            fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        except FileExistsError:
            pass
        else:
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                os.close(fd)
                raise
            return fd
    fd = os.open(name, flags, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('Unsafe registry file')
    except (OSError, ValueError):
        os.close(fd)
        raise
    return fd


@contextmanager
def locked_registry(*, write):
    created = False
    if write:
        try:
            ROOT.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
    # O_PATH also lets us pin a newly created directory under a restrictive umask.
    directory = os.open(ROOT, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe registry directory')
        if created and stat.S_IMODE(info.st_mode) != 0o700:
            os.chmod(f'/proc/self/fd/{directory}', 0o700)
        lock = checked_open(directory, 'lock', os.O_RDWR if write else os.O_RDONLY, create=write)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            yield directory, read_state(directory)
        finally:
            os.close(lock)
    finally:
        os.close(directory)


def read_state(directory):
    try:
        with os.fdopen(checked_open(directory, 'tasks.json', os.O_RDONLY)) as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except RecursionError as error:
        raise ValueError('Invalid registry: JSON nesting is too deep') from error
    if not isinstance(state, dict):
        raise ValueError('Invalid registry')
    for token, task in state.items():
        if not re.fullmatch('[0-9a-f]{32}', token) or not isinstance(task, dict):
            raise ValueError('Invalid registry entry')
        if (task.get('mode') not in ('Manual', 'Codex')
                or type(task.get('started')) not in (int, float)
                or not 0 <= task['started'] <= 1e12
                or not math.isfinite(task['started'])
                or not all(isinstance(task.get(k), str) for k in ('project', 'label'))):
            raise ValueError('Invalid registry entry')
        if task['mode'] == 'Codex' and (type(task.get('pid')) is not int
                or task['pid'] <= 0 or not isinstance(task.get('identity'), list)
                or len(task['identity']) != 2
                or not all(isinstance(item, str) and item for item in task['identity'])):
            raise ValueError('Invalid process identity')
        # Never echo control characters from a damaged or hand-edited registry.
        task['project'] = clean(task['project'], 80)
        task['label'] = task_label(task['label'])
    return state


def save(directory, state):
    fd, path = tempfile.mkstemp(prefix='.tasks-', dir=f'/proc/self/fd/{directory}')
    name = os.path.basename(path)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, 'tasks.json', src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def deliver(message, phase, code, stream, full_prompt=None, reminder=(), inspect_command=""):
    # Broken stdout or an inaccessible terminal must not roll back a committed END.
    try:
        print(message, file=stream, flush=True)
        if inspect_command:
            print("Read-only inspection: " + inspect_command, file=stream, flush=True)
    except (OSError, UnicodeError):
        pass
    if reminder:
        try:
            print('\n'.join(reminder), file=stream, flush=True)
        except (OSError, UnicodeError):
            pass
    color = '36' if phase == 'START' else '32' if code == 0 else '31'
    bell = '\a' if phase == 'END' else ''
    payload = f'\r\n{bell}\x1b[1;{color}m{message}\x1b[0m\r\n'.encode()
    if full_prompt is not None:
        payload += full_prompt.encode(errors='surrogateescape')
        if not payload.endswith(b'\n'):
            payload += b'\r\n'
    if reminder:
        payload += ('\r\n'.join(reminder) + '\r\n').encode()
    try:
        entries = os.listdir('/dev/pts')
    except OSError:
        return
    for entry in entries:
        if not re.fullmatch(r'\d+', entry):
            continue
        fd = None
        try:
            path = '/dev/pts/' + entry
            info = os.stat(path, follow_symlinks=False)
            if info.st_uid != os.getuid() or not stat.S_ISCHR(info.st_mode):
                continue
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_NOFOLLOW)
            opened = os.fstat(fd)
            if (opened.st_uid == os.getuid() and stat.S_ISCHR(opened.st_mode)
                    and opened.st_rdev == info.st_rdev):
                os.write(fd, payload)
        except OSError:
            pass
        finally:
            if fd is not None:
                os.close(fd)


class UnknownTask(ValueError):
    """END was requested for a token absent from the registry."""


def start(project, label, *, pid=None, resume=None, show_full_prompt=False,
          inspect_command='', notify=None):
    """Return a token, calling notify after commit and before releasing the lock."""
    task = dict(started=time.time(), project=clean(project, 80),
                label=task_label(label), mode='Manual')
    if pid is not None:
        if type(pid) is not int or pid <= 0:
            raise ValueError('Cannot identify Codex runner')
        stamp = identity(pid)
        if stamp is None:
            raise ValueError('Cannot identify Codex runner')
        task.update(mode='Codex', pid=pid, identity=stamp)
    if resume is not None:
        from codex_notice import session_id
        resume = session_id(resume)
        if resume is None:
            raise ValueError('Invalid resume session ID')
    full_prompt = label if show_full_prompt else None
    if inspect_command:
        if not re.fullmatch(r'tmux -S /tmp/codex-inspect\.[A-Za-z0-9_]{8}/socket attach -r -t codex-inspect-[A-Za-z0-9_]{8}', inspect_command):
            raise ValueError('Invalid inspection command')
        full_prompt = 'Read-only inspection: ' + inspect_command + '\n\n' + (full_prompt or '')

    with locked_registry(write=True) as (directory, state):
        state = {key: value for key, value in state.items() if not stale(value)}
        token = secrets.token_hex(16)
        while token in state:
            token = secrets.token_hex(16)
        state[token] = task
        save(directory, state)
        if notify is not None:
            suffix = ''
            if full_prompt is None:
                prefix = ' — ' if task['mode'] == 'Manual' else ' '
                suffix = prefix + task['label']
            if task['mode'] == 'Codex':
                suffix += f' | task: {token[:12]}'
                if resume:
                    suffix += f' | resume: {resume}'
            notify(summary(state, '🚀 START', task) + suffix, 'START', 0,
                   full_prompt=full_prompt, inspect_command=inspect_command, token=token)
    return token


def end(token, code, *, metadata=None, quota_enabled=False, notify=None, codex_cwd=None):
    """Finish a task or raise UnknownTask; publish only after a successful save."""
    if not re.fullmatch('[0-9a-f]{32}', token) or type(code) is not int or code < 0:
        raise ValueError('Invalid token or exit code')
    if code > 255:
        raise ValueError('Exit code must be 0..255')
    fields = []
    quota_home = None
    if notify is not None:
        if metadata:
            from codex_notice import notice_fields
            fields = notice_fields(metadata, codex_cwd)
        if quota_enabled:
            try:
                quota_home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
                if codex_cwd is not None:
                    quota_home = codex_cwd / quota_home
                quota_home = quota_home.absolute()
            except (OSError, RuntimeError, ValueError):
                pass

    with locked_registry(write=True) as (directory, state):
        if token not in state:
            raise UnknownTask('Unknown or expired token')
        # Preserve the END target long enough to finish it, even if stale.
        task = state.pop(token)
        state = {key: value for key, value in state.items() if not stale(value)}
        duration = max(0, int(time.time() - task['started']))
        save(directory, state)
        if notify is not None:
            marker = '✅ END' if code == 0 else '❌ ERROR'
            if task['mode'] == 'Codex':
                suffix = ' ' + ' | '.join([f'duration: {duration}s', f'task: {token[:12]}', *fields])
            else:
                suffix = f' ({duration}s)'
            reminder = ()
            if quota_enabled and task['mode'] == 'Codex':
                reminder = ('Codex usage: unavailable',)
                if quota_home is not None:
                    try:
                        from codex_notice import quota_reminder
                        reminder = quota_reminder(quota_home, Path(f'/proc/self/fd/{directory}/quota.json'))
                    except Exception:
                        # Optional session data must never prevent END delivery.
                        pass
            notify(summary(state, marker, task) + suffix, 'END', code, reminder=reminder)


def status():
    """Return (active tasks, stale count), or None if the registry is absent."""
    if not ROOT.exists() and not ROOT.is_symlink():
        return None
    with locked_registry(write=False) as (_, state):
        active = [task for task in state.values() if not stale(task)]
        return active, len(state) - len(active)


def summary(state, marker, task):
    mode = 'ChatGPT' if task['mode'] == 'Manual' else task['mode']
    return (f"[{time.strftime('%H:%M:%S')}] 🤖 {len(state)} {marker} "
            f"{mode} [{task['project']}]")
