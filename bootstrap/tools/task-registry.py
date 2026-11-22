#!/usr/bin/env python3
"""Command-line adapters for the shared task registry."""
import os
import re
import sys
import time

from task_registry import UnknownTask, deliver, end, start, status


def cli_notify(message, phase, code, *, token=None, **details):
    deliver(message, phase, code, sys.stderr if phase == 'START' else sys.stdout, **details)
    if token is not None:
        print(token, flush=True)


def main(args, *, manual=False):
    usage = 'Usage: start PROJECT LABEL [CODEX_PID [RESUME_ID]] | end TOKEN EXIT_CODE [METADATA_FILE] | status'
    command = args[0] if args else ''
    if manual:
        arity = {'start': 3, 'end': 3, 'status': 1}.get(command)
        if arity is None:
            print('Usage: manual-task.sh start PROJECT LABEL | end TOKEN EXIT_CODE | status', file=sys.stderr)
            return 2
        if len(args) != arity:
            return 2

    if command == 'start' and len(args) in (3, 4, 5):
        pid = int(args[3]) if len(args) >= 4 else None
        resume = args[4] if len(args) == 5 else None
        start(args[1], args[2], pid=pid, resume=resume, notify=cli_notify,
              show_full_prompt=os.environ.get('CODEX_STARTER_KIT_SHOW_FULL_PROMPT') == '1',
              inspect_command=os.environ.get('CODEX_STARTER_KIT_INSPECT_COMMAND', '') if pid is not None else '')
    elif command == 'end' and len(args) in (3, 4):
        if not re.fullmatch('[0-9]{1,3}', args[2]):
            raise ValueError('Invalid token or exit code')
        try:
            end(args[1], int(args[2]), metadata=args[3] if len(args) == 4 else None,
                quota_enabled=os.environ.get('CODEX_STARTER_KIT_LIMIT_REMINDER') == '1',
                notify=cli_notify)
        except UnknownTask as error:
            print(error, file=sys.stderr)
            return 3
    elif args == ['status']:
        snapshot = status()
        if snapshot is None:
            print('🤖 0 active tasks')
            return 0
        active, stale_count = snapshot
        print(f'🤖 {len(active)} active tasks ({stale_count} stale)')
        if active:
            print()
        for task in active:
            started = time.strftime('%Y-%m-%d %H:%M', time.localtime(task['started']))
            print(f"{started} {task['mode']} [{task['project']}]")
            print(f"  {task['label']}")
    else:
        raise ValueError(usage)
    return 0


if __name__ == '__main__':
    args = sys.argv[1:]
    manual = args[:1] == ['--manual']
    if manual or args[:1] == ['--notify']:
        args = args[1:]
    try:
        sys.exit(main(args, manual=manual))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Task registry: {error}', file=sys.stderr)
        sys.exit(2)
