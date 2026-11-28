"""Resource coordination and crash fences for cooperating Linux launchers."""
from contextlib import ExitStack, contextmanager
import errno
import fcntl
import hashlib
import json
import os
import re
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
def flock_file(directory, name, mode=fcntl.LOCK_EX):
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
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise ProjectBusy from error
        yield fd
    finally:
        # LOCK_UN would also release the lock for inherited copies.
        os.close(fd)


exclusive = flock_file


def check_reservation(fd, directory, name, *, writable=True):
    received = os.fstat(fd)
    expected = os.stat(name, dir_fd=directory, follow_symlinks=False)
    check_file(received)
    check_file(expected)
    if (received.st_dev, received.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError('Reservation belongs to a different project')
    # Linux reports an OFD's flock on its fdinfo, including for inherited FDs.
    # A separate open of the same inode is insufficient evidence of a reservation.
    with open(f'/proc/self/fdinfo/{fd}', encoding='ascii') as info:
        mode = 'WRITE' if writable else 'READ'
        if not any(line.split()[2:5] == ['FLOCK', 'ADVISORY', mode]
                   for line in info if line.startswith('lock:')):
            description = 'an exclusive' if writable else 'a shared'
            raise ValueError(f'Reservation descriptor does not hold {description} lock')


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
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass
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


def root_identity(project):
    """Identify the directory and its ancestry, including alternate mount aliases."""
    chain = []
    for path in (project, *project.parents):
        info = path.stat()
        chain.append([info.st_dev, info.st_ino])
    return chain


def check_mount_layout(project):
    # A bind mount of a subtree can hide its ancestry from path and inode checks.
    with open('/proc/self/mountinfo', 'rb') as stream:
        for line in stream:
            fields = line.split()
            root, target = fields[3:5]
            mount = Path(os.fsdecode(re.sub(rb'\\([0-7]{3})',
                                           lambda match: bytes((int(match[1], 8),)), target)))
            if project in mount.parents or (root != b'/' and
                                           (mount == project or mount in project.parents)):
                raise ValueError(f'Unsupported mount layout for resource: {project}')


def checked_root(path):
    project = canonical_project(path)
    key(project)
    check_mount_layout(project)
    # Git indirection may share writable metadata outside this reservation.
    for directory in (project, *project.parents):
        marker = directory / '.git'
        if marker.is_symlink() or marker.exists() and (
                directory != project or not marker.is_dir() or os.path.lexists(marker / 'commondir')):
            raise ValueError(f'Unsupported Git metadata layout: {marker}')
    if any(os.environ.get(name) for name in
           ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE',
            'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')):
        raise ValueError('Git environment redirects resources outside project coordination')
    return project


def task_resources(project):
    """Snapshot all configured links, using RO for duplicate external aliases."""
    project = checked_root(project)
    modes = {project: 'rw'}
    for mode in ('rw', 'ro'):
        directory = Path.home() / f'.codex-starter-kit-repos-{mode}'
        try:
            info = directory.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f'Expected a repository configuration directory: {directory}')
        for link in directory.iterdir():
            if not link.is_symlink():
                continue
            external = checked_root(link)
            if external == project and mode == 'ro':
                raise ValueError(f'Main project is configured read-only: {project}')
            modes[external] = mode
    resources = {str(root): {'mode': mode, 'identity': root_identity(root)}
                 for root, mode in sorted(modes.items())}
    validate_roots(resources)
    return resources


def overlaps(first, first_identity, second, second_identity):
    return (first == second or first in second.parents or second in first.parents
            or first_identity[0] in second_identity or second_identity[0] in first_identity)


def validate_roots(resources):
    previous = []
    for name, resource in resources.items():
        path = Path(name)
        identity = resource['identity']
        if any(overlaps(path, identity, other, chain) for other, chain in previous):
            raise ValueError(f'Overlapping resource roots: {path}')
        previous.append((path, identity))


def publish(directory, name, payload, *, live=False):
    """Publish an immutable record, optionally keeping its writer lease open."""
    fd, path = tempfile.mkstemp(prefix='.coord-', dir=f'/proc/self/fd/{directory}')
    temporary = os.path.basename(path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(os.dup(fd), 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if live:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        sync_directory(directory)
        return os.dup(fd) if live else None
    finally:
        os.close(fd)
        os.unlink(temporary, dir_fd=directory)


def names_in(directory):
    return os.listdir(f'/proc/self/fd/{directory}')


def open_record(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        check_file(os.fstat(fd), single_link=False)
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_resource_fences(directory, resource_key, names, *, shared=False):
    check_fence(directory, resource_key + '.unknown')
    prefix = resource_key + '.reader-'
    for name in names:
        if not name.startswith(prefix):
            continue
        try:
            fd = open_record(directory, name)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            raise ProjectUnknown from error
        try:
            previous = os.read(fd, 128)
            if not valid_boot_id(previous):
                raise ProjectUnknown
            if previous != boot_id():
                # Several readers may concurrently inspect a stale fence.
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    pass
                sync_directory(directory)
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                if shared:
                    continue
            # A concurrent reader may have cleared its fence before closing.
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ProjectUnknown
        finally:
            os.close(fd)


def valid_identity(identity):
    return (isinstance(identity, list) and bool(identity)
            and all(isinstance(pair, list) and len(pair) == 2
                    and all(type(number) is int for number in pair) for pair in identity))


def registered_roots(directory, names):
    for name in names:
        if not name.endswith('.root'):
            continue
        with os.fdopen(open_record(directory, name), 'rb') as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('Resource root record exceeds size limit')
        record = json.loads(data)
        if not isinstance(record, list) or len(record) != 2 or not isinstance(record[0], str):
            raise ValueError('Invalid resource root record')
        name_path, identity = record
        path = Path(name_path)
        if not path.is_absolute() or name != key(path) + '.root' or not valid_identity(identity):
            raise ValueError('Invalid resource root record')
        yield path, identity


@contextmanager
def reserve_resources(resources):
    """Acquire the complete set under a short admission lock, or release everything."""
    validate_roots(resources)
    with lock_directory() as directory, ExitStack() as held:
        with exclusive(directory, 'resource-admission'):
            names = set(names_in(directory))
            known = dict(registered_roots(directory, names))
            requested = {Path(name): info for name, info in resources.items()}
            identifiable = {key(path) + '.unknown' for path in known.keys() | requested.keys()}
            # Legacy fences contain only the boot ID. An unmapped current fence
            # could cover an ancestor or descendant of a requested root.
            for name in names - identifiable:
                if name.endswith('.unknown'):
                    check_fence(directory, name)
            for path, info in requested.items():
                if checked_root(path) != path or root_identity(path) != info['identity']:
                    raise ValueError(f'Resource changed during admission: {path}')
                for other, identity in known.items():
                    if path == other:
                        if identity != info['identity']:
                            check_resource_fences(directory, key(other), names)
                            with exclusive(directory, key(other) + '.lock'):
                                os.unlink(key(other) + '.root', dir_fd=directory)
                            names.remove(key(other) + '.root')
                        continue
                    if overlaps(path, info['identity'], other, identity):
                        with exclusive(directory, key(other) + '.lock'):
                            check_resource_fences(directory, key(other), names)
            fds = {}
            for name, info in sorted(resources.items()):
                path = Path(name)
                resource_key = key(path)
                shared = info['mode'] == 'ro'
                fds[name] = held.enter_context(flock_file(
                    directory, resource_key + '.lock', fcntl.LOCK_SH if shared else fcntl.LOCK_EX))
                record = resource_key + '.root'
                if record not in names:
                    publish(directory, record, json.dumps([name, info['identity']]).encode())
                if not shared:
                    with exclusive(directory, resource_key + '.active'):
                        check_resource_fences(directory, resource_key, names)
                else:
                    check_resource_fences(directory, resource_key, names, shared=True)
        yield fds


@contextmanager
def reserve_project(path):
    """Compatibility API for callers that reserve only a primary project."""
    project = checked_root(path)
    name = str(project)
    with reserve_resources({name: {'mode': 'rw', 'identity': root_identity(project)}}) as fds:
        yield fds[name]


@contextmanager
def project_execution(project, reservation_fd=None, *, resources=None, resource_fds=None, reserved_modes=None):
    """Verify a borrowed reservation or acquire a complete direct reservation."""
    name = str(canonical_project(project))
    if resources is None:
        resources = task_resources(project)
    if reservation_fd is None and resource_fds is None:
        with reserve_resources(resources) as fds:
            with project_execution(project, resources=resources, resource_fds=fds) as locks:
                yield locks
        return
    if resource_fds is None:
        if set(resources) != {name}:
            raise ValueError('A single reservation descriptor cannot cover external resources')
        resource_fds = {name: reservation_fd}
    with lock_directory() as directory, ExitStack() as active:
        names = names_in(directory)
        execution_fds = []
        for resource, info in sorted(resources.items()):
            resource_key = key(Path(resource))
            shared = info['mode'] == 'ro'
            reserved = (reserved_modes or resources)[resource]['mode']
            if not shared and reserved != 'rw':
                raise ValueError('Writable task resource lacks a writable reservation')
            check_reservation(resource_fds[resource], directory, resource_key + '.lock',
                              writable=reserved == 'rw')
            if not shared:
                execution_fds.append(active.enter_context(exclusive(directory, resource_key + '.active')))
            check_resource_fences(directory, resource_key, names, shared=shared)
        yield tuple(resource_fds[resource] for resource in resources) + tuple(execution_fds)


class ExecutionFences:
    """Keep shared reader fences live only while their supervisor is alive."""

    def __init__(self):
        self.writers = []
        self.readers = []

    def arm(self, resources):
        for name, info in resources.items():
            if info['mode'] == 'rw':
                self.writers.append(arm_fence(Path(name)))
            else:
                fence = key(Path(name)) + '.reader-' + uuid.uuid4().hex
                with lock_directory() as directory:
                    self.readers.append((fence, publish(directory, fence, boot_id(), live=True)))

    def clear(self):
        for name in [*self.writers, *(name for name, _ in self.readers)]:
            clear_fence(name)

    def close(self):
        for _, fd in self.readers:
            os.close(fd)
