"""Exclusive project coordination for cooperating Linux launchers of one user."""
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import uuid


LOCK_ROOT = Path('/tmp') / f'codex-starter-kit-project-locks-{os.getuid()}'
BUSY_EXIT = 75
UNKNOWN_EXIT = 76
RESERVATION_OPTION = '--reservation-fd'


class ProjectBusy(Exception):
    """A project reservation or execution is already active."""


class ProjectUnknown(Exception):
    """A previous execution has not been proved quiescent."""


def require_verified_execution_profile():
    """Reserve batch launches for a future, kernel-enforced isolation profile."""
    raise ValueError('Protected execution unavailable: no verified isolation profile for locks and receipts')


def canonical_project(path):
    project = Path(path).resolve(strict=True)
    if not project.is_dir():
        raise ValueError('Project directory must exist')
    return project


@contextmanager
def lock_directory():
    created = False
    try:
        LOCK_ROOT.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    fd = os.open(LOCK_ROOT, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe project lock directory')
        if created:
            os.chmod(f'/proc/self/fd/{fd}', 0o700)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise ValueError('Unsafe project lock directory permissions')
        yield fd
    finally:
        os.close(fd)


def key(project):
    root = LOCK_ROOT.parent.resolve() / LOCK_ROOT.name
    if project == root or project in root.parents or root in project.parents:
        raise ValueError('Project overlaps the private lock directory')
    return hashlib.sha256(os.fsencode(project)).hexdigest()


def check_file(info, *, single_link=True):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or single_link and info.st_nlink != 1):
        raise ValueError('Unsafe project lock file')


@contextmanager
def exclusive(directory, name):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        created = True
    except FileExistsError:
        fd = os.open(name, flags, dir_fd=directory)
        created = False
    try:
        if created:
            os.fchmod(fd, 0o600)
        check_file(os.fstat(fd))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise ProjectBusy from error
        yield fd
    finally:
        # LOCK_UN would also release the lock for inherited copies.
        os.close(fd)


def check_reservation(fd, directory, name):
    received = os.fstat(fd)
    expected = os.stat(name, dir_fd=directory, follow_symlinks=False)
    check_file(received)
    check_file(expected)
    if (received.st_dev, received.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError('Reservation belongs to a different project')
    # Linux reports an OFD's flock on its fdinfo, including for inherited FDs.
    # A separate open of the same inode is insufficient evidence of a reservation.
    with open(f'/proc/self/fdinfo/{fd}', encoding='ascii') as info:
        if not any(line.split()[2:5] == ['FLOCK', 'ADVISORY', 'WRITE']
                   for line in info if line.startswith('lock:')):
            raise ValueError('Reservation descriptor does not hold an exclusive lock')


def boot_id():
    raw = Path('/proc/sys/kernel/random/boot_id').read_bytes()
    if not valid_boot_id(raw):
        raise ValueError('Invalid Linux boot ID')
    return raw


def valid_boot_id(raw):
    try:
        return raw == (str(uuid.UUID(raw.decode('ascii').strip())) + '\n').encode('ascii')
    except ValueError:
        return False


def sync_directory(directory):
    fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY, dir_fd=directory)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def check_fence(directory, name):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ProjectUnknown from error
    try:
        with os.fdopen(fd, 'rb') as stream:
            check_file(os.fstat(stream.fileno()), single_link=False)
            previous = stream.read(128)
        current = boot_id()
    except (OSError, ValueError) as error:
        raise ProjectUnknown from error
    if previous == current or not valid_boot_id(previous):
        raise ProjectUnknown
    # A complete boot ID from a previous Linux boot cannot have surviving processes.
    os.unlink(name, dir_fd=directory)
    sync_directory(directory)


def arm_fence(project):
    """Publish a durable fence before launching anything that may write."""
    name = key(project) + '.unknown'
    with lock_directory() as directory:
        fd, path = tempfile.mkstemp(prefix='.fence-', dir=f'/proc/self/fd/{directory}')
        temporary = os.path.basename(path)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(boot_id())
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            os.unlink(temporary, dir_fd=directory)
        sync_directory(directory)
    return name


def clear_fence(name):
    """Called under the execution lease only after the process tree is empty."""
    with lock_directory() as directory:
        os.unlink(name, dir_fd=directory)
        sync_directory(directory)


@contextmanager
def reserve_project(path):
    """Hold a project for a future batch; pass this FD explicitly to its runner."""
    project = canonical_project(path)
    project_key = key(project)
    with lock_directory() as directory:
        with exclusive(directory, project_key + '.lock') as fd:
            with exclusive(directory, project_key + '.active'):
                check_fence(directory, project_key + '.unknown')
            yield fd


@contextmanager
def project_execution(project, reservation_fd=None):
    """Use a reservation OFD or acquire our own, then admit at most one task."""
    project_key = key(canonical_project(project))
    name = project_key + '.lock'
    with lock_directory() as directory:
        if reservation_fd is None:
            with exclusive(directory, name) as fd:
                with exclusive(directory, project_key + '.active') as active:
                    check_fence(directory, project_key + '.unknown')
                    yield fd, active
        else:
            check_reservation(reservation_fd, directory, name)
            with exclusive(directory, project_key + '.active') as active:
                check_fence(directory, project_key + '.unknown')
                yield reservation_fd, active
