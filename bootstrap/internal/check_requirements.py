"""Read-only checks for the bootstrap runtime profiles."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def manager_reachable():
    try:
        return subprocess.run(['systemctl', '--user', 'show-environment'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def main(profile):
    tty = sys.stdout.isatty()
    failures = 0
    total = 0

    def report(ok, label, success, failure):
        nonlocal failures, total
        total += 1
        failures += not ok
        mark, status = ('✅', 'OK') if ok else ('❌', 'KO')
        status = f'{mark} {status:<4}'
        if tty:
            color = '32' if ok else '31'
            status = f'\033[{color}m{status}\033[0m'
        print(f'{status} {label}: {success if ok else failure}')

    print(f'Requirements profile: {profile}')
    report(sys.version_info >= (3, 8), 'python3', 'available', 'Python 3.8 or newer is required')
    commands = ['bash', 'dirname']
    if profile in ('service', 'all'):
        commands += ['tmux', 'node', 'npx', 'systemctl']
    if profile in ('tools', 'all'):
        commands += ['git', 'codex']
    for name in commands:
        report(shutil.which(name) is not None, name, 'available', 'missing from PATH; provide it before setup')

    if profile in ('hook', 'tools', 'all'):
        proc = Path('/proc/self')
        readable = all(os.access(proc / name, os.R_OK) for name in ('comm', 'status', 'stat', 'cmdline'))
        readable = (readable and (proc / 'exe').exists()
                    and os.access('/proc/sys/kernel/random/boot_id', os.R_OK)
                    and (proc / 'fd').is_dir())
        report(readable, '/proc', 'Linux process and boot information readable', 'readable Linux procfs required')

    if profile in ('hook', 'service', 'all'):
        paths = ['/usr/bin/bash']
        if profile in ('service', 'all'):
            paths.append('/usr/bin/tmux')
        for path in paths:
            report(Path(path).is_file() and os.access(path, os.X_OK), path, 'executable',
                   'required by generated hook or unit')
    if profile in ('service', 'all'):
        report(manager_reachable(), 'systemctl --user', 'user manager reachable', 'user manager unavailable')
    print(f'Summary: {total - failures} OK, {failures} KO')
    return int(bool(failures))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
