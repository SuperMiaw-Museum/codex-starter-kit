"""Offline Bubblewrap launch profile for the trusted direct-runner interface."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import shutil
import stat


BWRAP = '/usr/bin/bwrap'


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def mounted_paths():
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        escaped = line.split()[4]
        yield Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), escaped))


class OfflineBubblewrap:
    """Mount only a project and a read-only host runtime, with isolated loopback."""

    def __init__(self, project, launcher, protected_paths):
        self.project = Path(project).resolve(strict=True)
        launcher = Path(os.path.abspath(launcher))
        self.toolchain = launcher.parent.parent.resolve(strict=True)
        if (launcher.name != 'codex' or launcher.parent.name != 'bin'
                or self.toolchain.parent.name != 'node' or self.toolchain.parent.parent.name != 'versions'
                or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', self.toolchain.name)
                or self.toolchain not in launcher.resolve(strict=True).parents
                or not (self.toolchain / 'bin/node').is_file()):
            raise ValueError('Offline isolation requires a local NVM npm Codex toolchain')
        self.protected_paths = tuple(Path(path) for path in protected_paths)

    def validate(self, project):
        if Path(project).resolve(strict=True) != self.project:
            raise ValueError('Isolation profile belongs to a different project')
        wrapper = Path(BWRAP).stat()
        if (not stat.S_ISREG(wrapper.st_mode) or wrapper.st_uid != 0
                or wrapper.st_mode & 0o022 or not os.access(BWRAP, os.X_OK)):
            raise ValueError('Unsafe Bubblewrap executable')
        runtime = Path('/usr').resolve(strict=True)
        if any(overlaps(self.project, path) for path in (runtime, self.toolchain)):
            raise ValueError('Project overlaps the mounted runtime')
        protected_ids = set()
        for path in self.protected_paths:
            protected = path.resolve()
            if any(overlaps(mount, protected) for mount in (self.project, runtime, self.toolchain)):
                raise ValueError('Mount overlaps a protected control path')
            try:
                info = protected.stat()
            except FileNotFoundError:
                continue
            protected_ids.add((info.st_dev, info.st_ino))
        for mount in mounted_paths():
            if mount == self.project or self.project in mount.parents:
                raise ValueError(f'Project contains a mount alias: {mount}')
            if runtime in mount.parents or mount == self.toolchain or self.toolchain in mount.parents:
                raise ValueError(f'Runtime contains a nested mount: {mount}')

        def reject_error(error):
            raise error

        for root, dirs, files in os.walk(self.project, onerror=reject_error):
            for path in (Path(root), *(Path(root) / name for name in dirs + files)):
                info = path.lstat()
                if (info.st_dev, info.st_ino) in protected_ids:
                    raise ValueError(f'Project aliases protected state: {path}')
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ValueError(f'Project contains a hard-linked file: {path}')
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
                        or stat.S_ISLNK(info.st_mode)):
                    raise ValueError(f'Project contains a special file: {path}')
        for root, dirs, files in os.walk(self.toolchain, onerror=reject_error):
            for name in dirs + files:
                path = Path(root) / name
                mode = path.lstat().st_mode
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                    raise ValueError(f'Toolchain contains a special file: {path}')

    def codex_argv(self, resume):
        if resume:
            raise ValueError('Offline isolation does not load host resumable sessions')
        return ['/toolchain/bin/codex', 'exec', '--skip-git-repo-check', '--ephemeral', '--json', '-']

    def command(self, resume):
        return [
            BWRAP, '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
            '--unshare-uts', '--unshare-cgroup-try', '--disable-userns', '--cap-drop', 'ALL',
            '--new-session', '--die-with-parent', '--clearenv',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--tmpfs', '/home', '--dir', '/home/worker', '--dir', '/home/worker/.codex',
            '--ro-bind', str(self.toolchain), '/toolchain', '--bind', str(self.project), '/project',
            '--chdir', '/project', '--setenv', 'HOME', '/home/worker',
            '--setenv', 'CODEX_HOME', '/home/worker/.codex',
            '--setenv', 'PATH', '/toolchain/bin:/usr/bin:/bin', '--setenv', 'LANG', 'C.UTF-8',
            '--', *self.codex_argv(resume),
        ]


@contextmanager
def sealed_input(task_file):
    """Expose prompt bytes without an FD referencing a named host file."""
    fd = os.memfd_create('codex-prompt', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    with os.fdopen(fd, 'w+b') as stream:
        with task_file.open('rb') as prompt:
            shutil.copyfileobj(prompt, stream)
        stream.flush()
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        stream.seek(0)
        yield stream
