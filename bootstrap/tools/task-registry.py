#!/usr/bin/env python3
"""Per-user task registry. All mutations and delivery share one advisory lock."""
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
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


def checked_open(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        os.close(fd)
        raise ValueError('Unsafe registry file')
    return fd


def read_state():
    try:
        with os.fdopen(checked_open('tasks.json', os.O_RDONLY)) as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
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


def save(state):
    fd, name = tempfile.mkstemp(prefix='.tasks-', dir='.')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, 'tasks.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def deliver(message, phase, code, stream, full_prompt=None, reminder=(), inspect_command=""):
    # Broken stdout or an inaccessible terminal must not roll back a committed END.
    try:
        print(message, file=stream, flush=True)
    except OSError:
        pass
    if inspect_command:
        print("Read-only inspection: " + inspect_command, file=stream, flush=True)
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


def main(args):
    usage = 'Usage: start PROJECT LABEL [CODEX_PID [RESUME_ID]] | end TOKEN EXIT_CODE [METADATA_FILE] | status'
    if not args:
        raise ValueError(usage)
    metadata = None
    full_prompt = None
    resume = None
    inspect_command = ""
    command = args[0]
    if command == 'start' and len(args) in (3, 4, 5):
        task = dict(started=time.time(), project=clean(args[1], 80),
                    label=task_label(args[2]), mode='Manual')
        if len(args) >= 4:
            pid = int(args[3])
            stamp = identity(pid)
            if stamp is None:
                raise ValueError('Cannot identify Codex runner')
            task.update(mode='Codex', pid=pid, identity=stamp)
        if len(args) == 5:
            from codex_notice import session_id
            resume = session_id(args[4])
            if resume is None:
                raise ValueError('Invalid resume session ID')
        if os.environ.get('CODEX_STARTER_KIT_SHOW_FULL_PROMPT') == '1':
            full_prompt = args[2]
        inspect_command = os.environ.get('CODEX_STARTER_KIT_INSPECT_COMMAND', '') if len(args) >= 4 else ''
        if inspect_command:
            if not re.fullmatch(r'tmux -S /tmp/codex-inspect\.[A-Za-z0-9]{8}/socket attach -r -t codex-inspect-[A-Za-z0-9]{8}', inspect_command):
                raise ValueError('Invalid inspection command')
            full_prompt = ('Read-only inspection: ' + inspect_command + '\n\n'
                           + (full_prompt if full_prompt is not None else ''))
    elif command == 'end' and len(args) in (3, 4):
        if not re.fullmatch('[0-9a-f]{32}', args[1]) or not re.fullmatch('[0-9]{1,3}', args[2]):
            raise ValueError('Invalid token or exit code')
        code = int(args[2])
        if code > 255:
            raise ValueError('Exit code must be 0..255')
        if len(args) == 4:
            metadata = args[3]
    elif args != ['status']:
        raise ValueError(usage)

    fields = []
    if metadata:
        from codex_notice import notice_fields
        fields = notice_fields(metadata)
    if command == 'status' and not ROOT.exists() and not ROOT.is_symlink():
        print('🤖 0 active tasks')
        return 0
    if command != 'status':
        ROOT.mkdir(mode=0o700, exist_ok=True)
    # Pin the directory with O_NOFOLLOW; relative operations cannot be redirected.
    directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe registry directory')
        os.fchdir(directory)
        lock = checked_open('lock', os.O_RDONLY if command == 'status' else os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(lock, fcntl.LOCK_SH if command == 'status' else fcntl.LOCK_EX)
            state = read_state()
            if command == 'status':
                active = [task for task in state.values() if not stale(task)]
                print(f'🤖 {len(active)} active tasks ({len(state) - len(active)} stale)')
                if active:
                    print()
                for task in active:
                    started = time.strftime('%Y-%m-%d %H:%M', time.localtime(task['started']))
                    print(f"{started} {task['mode']} [{task['project']}]")
                    print(f"  {task['label']}")
                return 0
            if command == 'end' and args[1] not in state:
                print('Unknown or expired token', file=sys.stderr)
                return 3
            # Preserve the END target long enough to finish it, even if stale.
            state = {key: value for key, value in state.items()
                     if (command == 'end' and key == args[1]) or not stale(value)}
            if command == 'start':
                token = secrets.token_hex(16)
                while token in state:
                    token = secrets.token_hex(16)
                state[token] = task
                marker = '🚀 START'
                if full_prompt is not None:
                    suffix = ''
                else:
                    prefix = ' — ' if task['mode'] == 'Manual' else ' '
                    suffix = prefix + task['label']
                if task['mode'] == 'Codex':
                    suffix += f' | task: {token[:12]}'
                    if resume:
                        suffix += f' | resume: {resume}'
            else:
                token = args[1]
                task = state.pop(token)
                marker = '✅ END' if code == 0 else '❌ ERROR'
                duration = max(0, int(time.time() - task['started']))
                if task['mode'] == 'Codex':
                    suffix = ' ' + ' | '.join([f'duration: {duration}s', f'task: {token[:12]}', *fields])
                else:
                    suffix = f' ({duration}s)'
            save(state)
            message = (f"[{time.strftime('%H:%M:%S')}] 🤖 {len(state)} {marker} "
                       f"{task['mode']} [{task['project']}]" + suffix)
            reminder = ()
            if (command == 'end' and task['mode'] == 'Codex'
                    and os.environ.get('CODEX_STARTER_KIT_LIMIT_REMINDER') == '1'):
                try:
                    from codex_notice import quota_reminder
                    reminder = quota_reminder()
                except Exception:
                    # Optional session data must never prevent delivery of the END notice.
                    pass
            deliver(message, command.upper(), 0 if command == 'start' else code,
                    sys.stderr if command == 'start' else sys.stdout, full_prompt, reminder, inspect_command)
            if command == 'start':
                print(token, flush=True)
        finally:
            os.close(lock)
    finally:
        os.close(directory)
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Task registry: {error}', file=sys.stderr)
        sys.exit(2)
