"""Stream selected Codex exec events and record only notification metadata."""
import json
import os
from pathlib import Path
import re
import sys


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


def capture(path):
    with open(path, 'w', encoding='utf-8', buffering=1) as metadata:
        for event in events(sys.stdin.buffer):
            kind = event.get('type')
            if kind == 'thread.started':
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
