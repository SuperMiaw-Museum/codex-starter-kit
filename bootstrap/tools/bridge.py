"""Manage the Desktop Commander foreground bridge and its service pane."""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys


TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))
from remote_control import SERVICE, service_status, show_reminder


SESSION = 'desktop-commander'
EXIT_CHANNEL = 'desktop-commander-exit'
BRIDGE = ['npx', '@wonderwhy-er/desktop-commander@latest', 'remote']
BUSY = {'active', 'activating', 'reloading', 'deactivating', 'refreshing'}


def command_status(command, *, quiet=False):
    output = subprocess.DEVNULL if quiet else None
    try:
        code = subprocess.run(command, stdout=output, stderr=output).returncode
    except OSError as error:
        if not quiet:
            print(error, file=sys.stderr)
        return 127 if isinstance(error, FileNotFoundError) else 126
    return 128 - code if code < 0 else code


def with_toolchain(bash, command):
    # NVM is a shell script. The fixed shell adapter forwards arguments verbatim.
    return [bash, '-e', '-o', 'pipefail', '-c', 'source "$1"; shift; exec "$@"', 'bridge-environment',
            str(TOOLS.parent / '.common-env.sh'), *command]


def foreground(bash):
    try:
        fd = os.open(TOOLS.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        print(error, file=sys.stderr)
        return 1
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print('Could not lock this checkout. A one-shot bridge may already be starting or running.',
                  file=sys.stderr)
            return 1
        status = service_status('')
        if status in BUSY:
            print(f'{SERVICE} is {status}. Stop it before starting a one-shot bridge.',
                  file=sys.stderr)
            return 1
        if command_status(['tmux', 'has-session', '-t', f'={SESSION}'], quiet=True) == 0:
            print('The desktop-commander tmux session already exists. Use or stop it first.',
                  file=sys.stderr)
            return 1
        os.set_inheritable(fd, True)
        command = with_toolchain(bash, [sys.executable, '-B', __file__, '--launch-foreground'])
        os.execv(bash, command)
    except OSError as error:
        print(error, file=sys.stderr)
        return 127 if isinstance(error, FileNotFoundError) else 126
    finally:
        os.close(fd)


def launch_foreground():
    try:
        show_reminder()
    except OSError:
        return 1
    try:
        os.execvp(BRIDGE[0], BRIDGE)
    except OSError as error:
        print(error, file=sys.stderr)
        return 127 if isinstance(error, FileNotFoundError) else 126


def inside_tmux(bash):
    # The pane must signal its waiter even when the child or NVM reports failure.
    process = None
    received = None

    def forward(number, _frame):
        nonlocal received
        if received is None:
            received = number
            if process is not None:
                process.send_signal(number)

    previous = {number: signal.signal(number, forward)
                for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)}
    try:
        try:
            with subprocess.Popen(with_toolchain(bash, BRIDGE)) as process:
                if received is not None:
                    process.send_signal(received)
                code = process.wait()
        except OSError as error:
            print(error, file=sys.stderr)
            code = 127 if isinstance(error, FileNotFoundError) else 126
    finally:
        command_status(['tmux', 'wait-for', '-S', EXIT_CHANNEL])
        for number, handler in previous.items():
            signal.signal(number, handler)
    if received is not None:
        return 128 + received
    return 128 - code if code < 0 else code


def main(bash, args):
    mode = args[0] if args else ''
    if mode == '--foreground':
        return foreground(bash)
    if mode == '--inside-tmux':
        return inside_tmux(bash)
    code = command_status(['tmux', 'new-session', '-d', '-s', SESSION,
                           str(TOOLS / 'desktop-commander.sh'), '--inside-tmux'])
    if code:
        return code
    return command_status(['tmux', 'wait-for', EXIT_CHANNEL])


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--launch-foreground']:
            sys.exit(launch_foreground())
        sys.exit(main(sys.argv[1], sys.argv[2:]))
    except KeyboardInterrupt:
        sys.exit(128 + signal.SIGINT)
