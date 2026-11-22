"""Preview or explicitly install the managed hook and user unit."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile


BOOTSTRAP = Path(__file__).resolve().parent
BEGIN = b'# BEGIN codex-starter-kit reminder'
END = b'# END codex-starter-kit reminder'


class SetupError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def unit_quote(value):
    escapes = {'\\': r'\\', '"': r'\"', '%': '%%', '\n': r'\n', '\r': r'\r', '\t': r'\t'}
    return ''.join(escapes.get(char, char) for char in value)


def render_unit(home):
    runner = unit_quote(str(BOOTSTRAP / 'tools/desktop-commander.sh')).replace('$', '$$')
    return (
        '# codex-starter-kit managed unit v1\n'
        '[Unit]\nDescription=Desktop Commander Remote\n'
        'After=network-online.target\nWants=network-online.target\n\n'
        '[Service]\nType=simple\n'
        f'Environment="HOME={unit_quote(str(home))}"\n'
        f'ExecStart=/usr/bin/bash "{runner}"\n'
        'ExecStop=-/usr/bin/tmux kill-session -t desktop-commander\n'
        'Restart=always\nRestartSec=15\n\n[Install]\nWantedBy=default.target\n'
    )


def read_regular(path, label):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(info.st_mode):
        raise SetupError(f'Refusing non-regular {label}')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise SetupError(f'Refusing non-regular {label}')
        return stream.read(), info


@contextmanager
def locked_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def atomic_write(path, data, directory, mode=0o600, *, times=None):
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            if times is not None:
                os.utime(stream.fileno(), ns=times)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.fsync(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def hook_content(original):
    reminder = os.fsencode(shlex.quote(str(BOOTSTRAP / 'remote-control.sh')))
    block = (BEGIN + b'\ncase $- in *i*) /usr/bin/bash ' + reminder + b' --auto ;; esac\n'
             + END + b'\n')
    inside = found = False
    output = []
    # Split only on LF so bytes such as CR in unmanaged lines are preserved.
    lines = original.split(b'\n')
    for index, line in enumerate(lines):
        newline = b'\n' if index < len(lines) - 1 else b''
        if line == BEGIN:
            if inside:
                raise SetupError('Malformed reminder block in .bashrc', 2)
            inside = True
            if not found:
                output.append(block)
                found = True
        elif line == END:
            if not inside:
                raise SetupError('Malformed reminder block in .bashrc', 2)
            inside = False
        elif not inside:
            output.append(line + newline)
    if inside:
        raise SetupError('Malformed reminder block in .bashrc', 2)
    if not found:
        if original and not original.endswith(b'\n'):
            output.append(b'\n')
        output.append(block)
    return b''.join(output)


def install_hook(home):
    path = home / '.bashrc'
    with locked_directory(home) as directory:
        original, info = read_regular(path, '.bashrc')
        desired = hook_content(original or b'')
        if original == desired:
            print('Reminder hook already current.')
        else:
            mode = stat.S_IMODE(info.st_mode) if info else 0o600
            atomic_write(path, desired, directory, mode)
            print('Reminder hook installed in ~/.bashrc (takes effect in a new interactive Bash).')


def install_service(path, desired):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f'.{path.name}.reload-pending')
    with locked_directory(path.parent) as directory:
        original, info = read_regular(path, 'service file')
        waiting, _ = read_regular(pending, 'reload marker')
        if original != desired:
            if info is not None:
                backup = path.with_suffix('.service.backup')
                atomic_write(backup, original, directory, stat.S_IMODE(info.st_mode),
                             times=(info.st_atime_ns, info.st_mtime_ns))
            # Record the work before publishing the unit. A crash before reload
            # may cause an extra reload, but cannot silently skip the needed one.
            atomic_write(pending, b'', directory)
            atomic_write(path, desired, directory)
        elif waiting is None:
            print('Service content already current.')

        if original != desired or waiting is not None:
            result = subprocess.run(['systemctl', '--user', 'daemon-reload']).returncode
            if result:
                print('Service unit written; daemon-reload failed. Rerun --install-service to retry.',
                      file=sys.stderr)
                return 128 - result if result < 0 else result
            pending.unlink()
            os.fsync(directory)
            print('Service unit installed/updated; daemon reloaded.')
    print('No service was started, stopped, restarted, or enabled.')
    print('When ready to disconnect/reconnect manually: systemctl --user restart desktop-commander.service')
    print('Optional boot/login activation: systemctl --user enable desktop-commander.service')
    return 0


def main(bash, mode):
    if sys.version_info < (3, 8):
        raise SetupError('Setup requires Python 3.8 or newer.')
    if not os.environ.get('HOME'):
        raise SetupError('Setup requires HOME to be set.')
    home = Path(os.environ['HOME'])
    config = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    unit = config / 'systemd/user/desktop-commander.service'
    profile = 'hook' if mode == '--install-hook' else 'service'
    result = subprocess.run([bash, str(BOOTSTRAP / 'check-requirements.sh'), profile]).returncode
    result = 128 - result if result < 0 else result
    if mode == '--preview':
        print(render_unit(home), end='')
        print(f'\nTarget: {unit}\nRun --install-hook or --install-service explicitly to apply.')
    elif result == 0:
        if mode == '--install-hook':
            install_hook(home)
        else:
            return install_service(unit, os.fsencode(render_unit(home)))
    return result


if __name__ == '__main__':
    try:
        sys.exit(main(*sys.argv[1:]))
    except SetupError as error:
        print(error, file=sys.stderr)
        sys.exit(error.code)
    except (OSError, KeyError) as error:
        print(f'Setup: {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
