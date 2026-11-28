"""Display the SSH reminder and launch the optional foreground bridge."""
import os
from pathlib import Path
import subprocess
import sys

from agent_ancestry import eligible


BOOTSTRAP = Path(__file__).resolve().parents[1]
SERVICE = 'desktop-commander.service'


def service_status(fallback):
    try:
        result = subprocess.run(['systemctl', '--user', 'is-active', SERVICE],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return fallback
    if result.returncode and fallback:
        return fallback
    return os.fsdecode(result.stdout).rstrip('\n')


def show_reminder():
    accent, reset = ('\033[1;36m', '\033[0m') if sys.stderr.isatty() else ('', '')
    try:
        version = os.fsdecode((BOOTSTRAP / 'version.txt').read_bytes()).rstrip('\n')
    except OSError:
        version = 'unknown'
    print(f'\n{accent}CODEX STARTER KIT · {version}{reset}\n', file=sys.stderr, flush=True)
    print(f'Remote Desktop Commander: {service_status("inactive") or "unknown"}',
          file=sys.stderr, flush=True)
    try:
        subprocess.run([str(BOOTSTRAP / 'orchestration/manual-task.sh'), 'status'], stdout=sys.stderr)
    except OSError:
        pass
    print(f'\n{accent}=== COPY THIS PROMPT INTO CHATGPT ==={reset}\n\n'
          f'Read "{BOOTSTRAP}/SYSTEM_PROMPT.md" via Remote Desktop Commander, treat it as '
          'operational guidance for this session, acknowledge, and wait for my next request.\n',
          file=sys.stderr, flush=True)


def main(invocation, args):
    mode = (args[0] if args else '') or '--show'
    if mode == '--once':
        if len(args) != 1:
            print(f'Usage: {invocation} --once', file=sys.stderr)
            return 2
        wrapper = BOOTSTRAP / 'internal/desktop-commander.sh'
        try:
            os.execv(wrapper, [str(wrapper), '--foreground'])
        except OSError as error:
            print(error, file=sys.stderr)
            return 127 if isinstance(error, FileNotFoundError) else 126
    if mode == '--auto':
        if not (sys.stdin.isatty() and sys.stderr.isatty() and eligible(os.getppid())):
            return 0
    elif mode != '--show':
        print(f'Usage: {invocation} [--show|--auto|--once]', file=sys.stderr)
        return 2
    try:
        show_reminder()
    except OSError:
        return 0 if mode == '--auto' else 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2:]))
