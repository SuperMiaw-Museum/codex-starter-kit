"""Collect local Codex metadata and quota snapshots for notifications."""
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time


QUOTA_WINDOWS = (('primary', 300, '⏱️ Codex 5-hour'), ('secondary', 10080, '📅 Codex weekly'))
QUOTA_STALE_AFTER = 15 * 60
QUOTA_TAIL_BYTES = 256 * 1024
QUOTA_MAX_DIRECTORIES = 24
QUOTA_MAX_ENTRIES = 1024
QUOTA_MAX_FILES = 24
MODEL_MAX_ENTRIES = 1024
MODEL_MAX_BYTES = 1024 * 1024
QUOTA_CACHE_BYTES = 4096
QUOTA_UNAVAILABLE = ('Codex usage: unavailable',)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def session_id(value):
    if isinstance(value, str) and re.fullmatch(
            r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
        return value
    return None


def token_total(input_tokens, output_tokens):
    counts = (input_tokens, output_tokens)
    if all(type(count) is int and 0 <= count < 10**15 for count in counts):
        return sum(counts)
    return None


def events(stream):
    for line in stream:
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(event, dict):
            yield event


def validated_quota(stamp, windows, now):
    if (type(stamp) not in (int, float) or not 0 < stamp <= now
            or not isinstance(windows, list) or len(windows) != len(QUOTA_WINDOWS)):
        return None
    for (_, minutes, _), window in zip(QUOTA_WINDOWS, windows):
        if not isinstance(window, list) or len(window) != 2:
            return None
        used, reset = window
        if (type(used) not in (int, float) or not 0 <= used <= 100
                or type(reset) not in (int, float)
                or not stamp < reset <= stamp + minutes * 60):
            return None
    return stamp, windows


def quota_snapshot(event, now):
    payload = event.get('payload')
    if (event.get('type') != 'event_msg' or not isinstance(payload, dict)
            or payload.get('type') != 'token_count'):
        return None
    raw_timestamp = event.get('timestamp')
    limits = payload.get('rate_limits')
    if not isinstance(raw_timestamp, str) or not isinstance(limits, dict):
        return None
    try:
        timestamp = datetime.fromisoformat(raw_timestamp.replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            return None
        stamp = timestamp.timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    windows = []
    for key, minutes, _ in QUOTA_WINDOWS:
        window = limits.get(key)
        if not isinstance(window, dict) or window.get('window_minutes') != minutes:
            return None
        windows.append([window.get('used_percent'), window.get('resets_at')])
    return validated_quota(stamp, windows, now)


def quota_tail(directory, name):
    with os.fdopen(os.open(name, FILE_FLAGS, dir_fd=directory), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return []
        offset = max(0, info.st_size - QUOTA_TAIL_BYTES)
        starts_on_boundary = True
        if offset:
            stream.seek(offset - 1)
            starts_on_boundary = stream.read(1) == b'\n'
        lines = stream.read(QUOTA_TAIL_BYTES).split(b'\n')
        # Drop any partial record at the start and any unfinished append at EOF.
        return lines[0 if starts_on_boundary else 1:-1]


def newest_quota_snapshot(home, now, deadline):
    newest = None
    directories_left = QUOTA_MAX_DIRECTORIES
    entries_left = QUOTA_MAX_ENTRIES
    files_left = QUOTA_MAX_FILES

    def scan(parent, name):
        nonlocal newest, directories_left, entries_left, files_left
        if not directories_left or not files_left or time.monotonic() >= deadline:
            return
        directories_left -= 1
        try:
            directory = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
        except OSError:
            return
        try:
            folders, files = [], []
            with os.scandir(directory) as entries:
                while entries_left and time.monotonic() < deadline:
                    entry = next(entries, None)
                    if entry is None:
                        break
                    entries_left -= 1
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            folders.append(entry.name)
                        elif entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False):
                            files.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name))
                    except OSError:
                        continue
            for _, filename in sorted(files, reverse=True):
                if not files_left or time.monotonic() >= deadline:
                    return
                files_left -= 1
                try:
                    for line in reversed(quota_tail(directory, filename)):
                        if time.monotonic() >= deadline:
                            return
                        if b'token_count' not in line:
                            continue
                        for event in events((line,)):
                            snapshot = quota_snapshot(event, now)
                            if snapshot and (newest is None or snapshot[0] > newest[0]):
                                newest = snapshot
                except OSError:
                    continue
            for folder in sorted(folders, reverse=True):
                if not entries_left or not directories_left or not files_left:
                    break
                scan(directory, folder)
        except OSError:
            return
        finally:
            os.close(directory)

    try:
        fd = os.open(home, DIRECTORY_FLAGS)
    except OSError:
        return None
    try:
        scan(fd, 'sessions')
    finally:
        os.close(fd)
    return newest


def read_quota_cache(cache, home, now):
    try:
        with os.fdopen(os.open(cache, FILE_FLAGS), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > QUOTA_CACHE_BYTES):
                return None
            record = json.loads(stream.read(QUOTA_CACHE_BYTES + 1))
        if isinstance(record, dict) and record.get('home') == str(home):
            return validated_quota(record.get('stamp'), record.get('windows'), now)
    except (OSError, ValueError, RecursionError):
        pass
    return None


def write_quota_cache(cache, home, snapshot):
    # The caller holds the registry lock. Atomic replacement also protects readers
    # from partial writes if an END process is interrupted.
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=cache.parent, prefix='.quota-',
                                         delete=False, encoding='utf-8') as stream:
            temporary = Path(stream.name)
            try:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(dict(home=str(home), stamp=snapshot[0], windows=snapshot[1]), stream)
                stream.close()
                temporary.replace(cache)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError:
        pass


def quota_duration(seconds):
    hours, minutes = divmod(seconds // 60, 60)
    days, hours = divmod(hours, 24)
    parts = ((days, 'd'), (hours, 'h'), (minutes, 'm'))
    return ' '.join(f'{amount}{unit}' for amount, unit in parts if amount) or f'{seconds}s'


def quota_reminder(home, cache):
    now = time.time()
    cached = read_quota_cache(cache, home, now)
    found = newest_quota_snapshot(home, now, time.monotonic() + 1)
    now = time.time()
    # Recheck after I/O in case a reset passed or the wall clock moved back.
    cached = cached and validated_quota(*cached, now)
    found = found and validated_quota(*found, now)
    snapshot = cached
    if found and (cached is None or found[0] > cached[0]):
        snapshot = found
        write_quota_cache(cache, home, found)
    if snapshot is None:
        return QUOTA_UNAVAILABLE
    now = time.time()
    stamp, windows = snapshot
    if stamp > now:
        return QUOTA_UNAVAILABLE
    age = now - stamp
    heading = f'Usage snapshot: {quota_duration(int(age))} ago'
    if any(reset <= now for _, reset in windows):
        return (heading + ' (expired)',)
    if age >= QUOTA_STALE_AFTER:
        return (heading + ' (stale)',)
    lines = [heading]
    for (_, _, label), (used, reset) in zip(QUOTA_WINDOWS, windows):
        countdown = quota_duration(math.ceil((reset - now) / 60) * 60)
        remaining = f'{100 - used:.2f}'.rstrip('0').rstrip('.')
        lines.append(f'{label}: {remaining}% remaining · resets in {countdown}')
    return tuple(lines)


def terminal_text(value):
    return ''.join(char if char.isprintable() or char in '\n\t'
                   else char.encode('unicode_escape').decode('ascii') for char in value)


def capture(path, session_output='', protected_output=''):
    render = terminal_text if protected_output else str
    session = None
    with open(path, 'w', encoding='utf-8', buffering=1) as metadata:
        for event in events(sys.stdin.buffer):
            kind = event.get('type')
            if kind == 'thread.started' and session is None:
                session = session_id(event.get('thread_id'))
                if session is not None:
                    print(json.dumps({'session': session}), file=metadata, flush=True)
            elif kind == 'turn.completed':
                usage = event.get('usage')
                if isinstance(usage, dict):
                    total = token_total(usage.get('input_tokens'), usage.get('output_tokens'))
                    if total is not None:
                        print(json.dumps({'tokens': total}), file=metadata, flush=True)
            elif kind in ('item.started', 'item.completed'):
                item = event.get('item')
                if not isinstance(item, dict):
                    continue
                if kind == 'item.completed' and item.get('type') == 'agent_message':
                    output = item.get('text')
                elif session_output and item.get('type') == 'command_execution':
                    output = (item.get('command') if kind == 'item.started'
                              else item.get('aggregated_output'))
                    if not output:
                        continue
                else:
                    continue
                if isinstance(output, str):
                    print(render(output), flush=True)
            elif kind in ('error', 'turn.failed'):
                error = event if kind == 'error' else event.get('error')
                if isinstance(error, dict) and isinstance(error.get('message'), str):
                    print(render(error['message']), file=sys.stderr, flush=True)
    if session is not None:
        marker = f'SANDBOX_HANDOFF_SESSION_ID={session}'
        print(marker, flush=True)
        if session_output:
            Path(session_output).write_text(marker + '\n', encoding='utf-8')


def notice_log_fd(path, flags):
    fd = os.open(path, flags | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('Notice logs must be regular files')
    except OSError:
        os.close(fd)
        raise
    return fd


def session_model(session, cwd=None):
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
    if cwd is not None:
        home = cwd / home
    remaining_entries = MODEL_MAX_ENTRIES
    remaining_bytes = MODEL_MAX_BYTES
    suffix = f'-{session}.jsonl'

    def candidates(directory, depth):
        nonlocal remaining_entries
        try:
            with os.scandir(directory) as entries:
                while remaining_entries:
                    entry = next(entries, None)
                    if entry is None:
                        break
                    remaining_entries -= 1
                    if depth and entry.is_dir(follow_symlinks=False):
                        yield from candidates(entry.path, depth - 1)
                    elif not depth and entry.name.startswith('rollout-') and entry.name.endswith(suffix):
                        yield entry.path
        except OSError:
            return

    for path in candidates(home / 'sessions', 3):
        if not remaining_bytes:
            break
        try:
            stream = open(path, 'rb', opener=notice_log_fd)
        except OSError:
            continue
        with stream:
            prefix = stream.read(remaining_bytes)
        remaining_bytes -= len(prefix)
        # Ignore an incomplete record at the byte limit or during an append.
        records = events(prefix.split(b'\n')[:-1])
        first = next(records, {})
        meta = first.get('payload')
        if (first.get('type') != 'session_meta' or not isinstance(meta, dict)
                or meta.get('id', meta.get('session_id')) != session):
            continue
        for record in records:
            payload = record.get('payload')
            if record.get('type') == 'turn_context' and isinstance(payload, dict):
                model = payload.get('model')
                if isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9._:/-]{1,100}', model):
                    return model
    return None


def notice_fields(path, cwd=None):
    session = None
    tokens = None
    try:
        with open(path, 'rb', opener=notice_log_fd) as stream:
            for event in events(stream):
                session = session_id(event.get('session')) or session
                total = event.get('tokens')
                if type(total) is int and 0 <= total < 2 * 10**15:
                    tokens = total
    except OSError:
        pass
    fields = []
    if session:
        try:
            model = session_model(session, cwd)
            if model:
                fields.append(f'model: {model}')
        except (OSError, RuntimeError, ValueError):
            pass
    if tokens is not None:
        fields.append(f'tokens: {tokens:,}')
    if session:
        fields.append(f'session: {session}')
    return fields


if __name__ == '__main__':
    capture(*sys.argv[1:])
