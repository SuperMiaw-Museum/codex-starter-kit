"""Linux process ancestry heuristic for automatic reminders."""
import fnmatch
import os
from pathlib import Path
import re
import sys


PROC = Path('/proc')
AGENTS = {
    'codex', 'codex-cli', 'desktop-commander', 'remote_desktop_commander',
    'remote-desktop-commander', 'run-codex.sh', 'desktop-commander.sh',
}


def is_agent(base):
    executable = Path(os.readlink(base / 'exe')).name
    argv = [os.fsdecode(arg) for arg in (base / 'cmdline').read_bytes().split(b'\0')]
    if not argv[0]:
        raise ValueError('Empty process command line')
    if executable in AGENTS or Path(argv[0]).name in AGENTS:
        return True
    python = re.fullmatch(r'python(?:3(?:\.\d+)?)?', executable)
    if python or executable in ('node', 'nodejs', 'bash', 'sh', 'dash'):
        index = 2 if python and argv[1:2] == ['-B'] else 1
        if len(argv) > index and argv[index] and not argv[index].startswith('-'):
            script = argv[index]
            return (Path(script).name in AGENTS
                    or python and script.endswith(('/bootstrap/tools/run_codex.py',
                                                   '/bootstrap/tools/bridge.py'))
                    or fnmatch.fnmatchcase(script, '*/@openai/codex/bin/codex.js')
                    or fnmatch.fnmatchcase(script, '*/@wonderwhy-er/desktop-commander/*'))
    return False


def eligible(pid):
    seen = set()
    ssh_chain = False
    root_session = False
    try:
        while pid > 1:
            if pid in seen:
                return False
            seen.add(pid)
            base = PROC / str(pid)
            fields = {}
            for line in (base / 'status').read_bytes().splitlines():
                key, _, value = line.partition(b':')
                if key in (b'Name', b'PPid', b'Uid'):
                    if key in fields:
                        return False
                    fields[key] = value.split()
            name, = fields[b'Name']
            parent, = fields[b'PPid']
            real, effective, *_ = fields[b'Uid']
            if (not re.fullmatch(rb'0|[1-9][0-9]*', parent)
                    or not real.isdigit() or real != effective
                    or (base / 'comm').read_bytes() != name + b'\n'):
                return False
            pid = int(parent)
            if name == b'sshd-session':
                if root_session and real != b'0':
                    return False
                root_session = real == b'0'
                ssh_chain = True
            elif name == b'sshd':
                return real == b'0' and pid == 1 and (not ssh_chain or root_session)
            elif ssh_chain or is_agent(base):
                return False
    except (OSError, ValueError, KeyError):
        return False
    return pid == 1 and not ssh_chain


if __name__ == '__main__':
    value = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        allowed = bool(re.fullmatch(r'0|[1-9][0-9]*', value)) and eligible(int(value))
    except ValueError:
        allowed = False
    sys.exit(0 if allowed else 1)
