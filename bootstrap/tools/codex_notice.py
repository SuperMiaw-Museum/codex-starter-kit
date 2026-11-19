"""Collect local Codex metadata and quota snapshots for notifications."""
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time


QUOTA_WINDOWS = (('primary', 300, '⏱️ Codex 5-hour'), ('secondary', 10080, '📅 Codex weekly'))
MAX_SESSION_LINE = 1024 * 1024


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


def quota_events(stream, deadline):
    oversized = False
    while time.monotonic() < deadline:
        line = stream.readline(MAX_SESSION_LINE + 1)
        if not line:
            return
        skip = oversized or len(line) > MAX_SESSION_LINE
        oversized = not line.endswith(b'\n')
        if not skip:
            yield from events((line,))
    raise TimeoutError


def quota_snapshot(event, now):
    payload = event.get('payload')
    if (event.get('type') != 'event_msg' or not isinstance(payload, dict)
            or payload.get('type') != 'token_count'):
        return None
    raw_timestamp = event.get('timestamp')
    if not isinstance(raw_timestamp, str):
        return None
    try:
        timestamp = datetime.fromisoformat(raw_timestamp.replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            return None
        stamp = timestamp.timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    limits = payload.get('rate_limits')
    if not now - 300 * 60 <= stamp <= now or not isinstance(limits, dict):
        return None
    windows = []
    for key, minutes, _ in QUOTA_WINDOWS:
        window = limits.get(key)
        if not isinstance(window, dict) or window.get('window_minutes') != minutes:
            return None
        used, reset = window.get('used_percent'), window.get('resets_at')
        if (type(used) not in (int, float) or not 0 <= used <= 100
                or type(reset) not in (int, float)
                or not now < reset <= stamp + minutes * 60):
            return None
        windows.append((used, reset))
    return stamp, windows


def newest_quota_snapshot(home, now, deadline):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(home / '.codex', flags)
    newest = None
    try:
        for _, _, names, directory in os.fwalk('sessions', dir_fd=fd):
            if time.monotonic() >= deadline:
                raise TimeoutError
            for name in names:
                if time.monotonic() >= deadline:
                    raise TimeoutError
                if not name.endswith('.jsonl'):
                    continue
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                           dir_fd=directory), 'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            continue
                        for event in quota_events(stream, deadline):
                            snapshot = quota_snapshot(event, now)
                            if snapshot and (newest is None or snapshot[0] >= newest[0]):
                                newest = snapshot
                except TimeoutError:
                    raise
                except OSError:
                    continue
    finally:
        os.close(fd)
    return newest


def quota_reminder():
    snapshot = newest_quota_snapshot(Path.home(), time.time(), time.monotonic() + 1)
    now = time.time()
    if snapshot is None or not now - 300 * 60 <= snapshot[0] <= now:
        return ()
    lines = []
    for (_, _, label), (used, reset) in zip(QUOTA_WINDOWS, snapshot[1]):
        if reset <= now:
            return ()
        hours, minutes = divmod(math.ceil((reset - now) / 60), 60)
        days, hours = divmod(hours, 24)
        countdown = ' '.join(f'{amount}{unit}' for amount, unit in
                             ((days, 'd'), (hours, 'h'), (minutes, 'm')) if amount)
        remaining = f'{100 - used:.2f}'.rstrip('0').rstrip('.')
        lines.append(f'{label}: {remaining}% remaining · resets in {countdown}')
    return tuple(lines)


def capture(path):
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
            elif kind == 'item.completed':
                item = event.get('item')
                if isinstance(item, dict) and item.get('type') == 'agent_message':
                    if isinstance(item.get('text'), str):
                        print(item['text'], flush=True)
            elif kind in ('error', 'turn.failed'):
                error = event if kind == 'error' else event.get('error')
                if isinstance(error, dict) and isinstance(error.get('message'), str):
                    print(error['message'], file=sys.stderr, flush=True)
    if session is not None:
        print(f'SANDBOX_HANDOFF_SESSION_ID={session}', flush=True)


def session_model(session):
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
    for path in (home / 'sessions').glob(f'*/*/*/rollout-*-{session}.jsonl'):
        with path.open('rb') as stream:
            records = events(stream)
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


def notice_fields(path):
    session = None
    tokens = None
    try:
        with open(path, 'rb') as stream:
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
            model = session_model(session)
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
    capture(sys.argv[1])
