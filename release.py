"""Validate versions and perform an explicitly confirmed Git release."""
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
VERSION_FILE = Path('bootstrap/version.txt')
LEVELS = ('major', 'minor', 'patch')
USAGE = """Usage: ./release.sh [--bump major|minor|patch] | --check | --help

Choose a bump interactively when omitted. Confirm once (default: no), update
bootstrap/version.txt, commit, create an annotated X.Y.Z tag, and atomically push
main and the tag to origin. Requires a clean checkout on main, synchronized
with origin/main, and an interactive terminal. No automatic rollback.
--check validates bootstrap/version.txt and CI tag context without writing.
GitHub Actions prepares a draft release for manual publication."""
RECOVERY = ('No rollback performed. Inspect git status, git log -1 and git tag before retrying; '
            'after a failed push, check origin. Local changes, commits or tags may remain.')


class ReleaseError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def git(*args, capture=True, check_error=None):
    result = subprocess.run(
        ['git', *args], cwd=ROOT, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'),
        stdout=subprocess.PIPE if capture else None,
    )
    if result.returncode:
        code = 128 - result.returncode if result.returncode < 0 else result.returncode
        command = args[2] if args[0] == '-c' else args[0]
        if check_error is not None:
            raise ReleaseError(f'{check_error} (git {command} exited {code})')
        raise ReleaseError(f'git {command} exited {code}', code)
    return result.stdout


def read_version():
    data = (ROOT / VERSION_FILE).read_bytes()
    component = rb'(0|[1-9][0-9]*)'
    if not re.fullmatch(rb'\.'.join([component] * 3) + rb'\n', data):
        raise ReleaseError('version.txt must contain plain a.b.c and a newline')
    return data[:-1].decode('ascii')


def write_version(target):
    path = ROOT / VERSION_FILE
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise ReleaseError('Refusing to update a non-regular bootstrap/version.txt.')
    # Replace the directory entry instead of writing through a link or truncating in place.
    fd, temporary = tempfile.mkstemp(prefix='.version.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write((target + '\n').encode('ascii'))
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(mode))
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def bump(version, level):
    parts = version.split('.')
    if any(len(part) > 9 for part in parts):
        raise ReleaseError('Version components are too large to bump safely.')
    index = LEVELS.index(level)
    parts[index] = str(int(parts[index]) + 1)
    parts[index + 1:] = ['0'] * (2 - index)
    return '.'.join(parts)


def object_id(ref, error):
    value = git('rev-parse', '--verify', ref, check_error=error)
    if not re.fullmatch(rb'(?:[0-9a-f]{40}|[0-9a-f]{64})\n', value):
        raise ReleaseError(error)
    return value[:-1]


def check_version(version):
    ref = os.environ.get('GITHUB_REF', '')
    tag = ref[len('refs/tags/'):] if ref.startswith('refs/tags/') else os.environ.get('CI_COMMIT_TAG', '')
    if tag or ref.startswith('refs/tags/'):
        if tag != version:
            raise ReleaseError('release tag must equal version.txt')
        error = 'release tag must point to HEAD'
        if object_id(f'refs/tags/{tag}^{{commit}}', error) != object_id('HEAD', error):
            raise ReleaseError(error)
    print(f'Version check passed: {version}', flush=True)


def require_clean(error):
    if git('status', '--porcelain', '--untracked-files=all', '--ignore-submodules=none',
           check_error=error):
        raise ReleaseError(error)


def require_main():
    error = 'Current branch must be main.'
    if git('symbolic-ref', '--quiet', '--short', 'HEAD', check_error=error) != b'main\n':
        raise ReleaseError(error)


def preflight():
    error = 'Run from this Git repository root.'
    if git('rev-parse', '--show-toplevel', check_error=error) != os.fsencode(ROOT) + b'\n':
        raise ReleaseError(error)
    require_clean('Release requires a clean worktree, including untracked files.')
    require_main()

    upstream = 'Require main tracking origin/main.'
    for key, expected in (('branch.main.remote', b'origin\n'),
                          ('branch.main.merge', b'refs/heads/main\n')):
        if git('config', '--get-all', key, check_error=upstream) != expected:
            raise ReleaseError(upstream)
    if git('rev-parse', '--abbrev-ref', '@{upstream}', check_error=upstream) != b'origin/main\n':
        raise ReleaseError(upstream)

    fetch_url = git('remote', 'get-url', '--all', 'origin')
    push_url = git('remote', 'get-url', '--push', '--all', 'origin')
    if not re.fullmatch(rb'[^\r\n]+\n', fetch_url) or fetch_url != push_url:
        raise ReleaseError('Require one identical fetch/push URL.')

    error = 'HEAD must match origin/main exactly; synchronize manually.'
    head = object_id('HEAD', error)
    if head != object_id('refs/remotes/origin/main', error):
        raise ReleaseError(error)
    if git('ls-remote', '--exit-code', 'origin', 'refs/heads/main') != head + b'\trefs/heads/main\n':
        raise ReleaseError('Remote main changed; synchronize manually.')
    git('var', 'GIT_AUTHOR_IDENT')
    git('var', 'GIT_COMMITTER_IDENT')
    return head, fetch_url


def require_new_tag(target):
    if git('tag', '--list', target, check_error=f'Could not check target tag locally: {target}'):
        raise ReleaseError(f'Target tag already exists: {target}')
    if git('ls-remote', '--tags', 'origin', f'refs/tags/{target}'):
        raise ReleaseError(f'Target tag already exists on origin: {target}')


def verify_commit(target, parent):
    version = read_version()
    check_version(version)
    if version != target:
        raise ReleaseError('Commit changed the target version; inspect before tagging.')
    require_clean('Commit left a dirty worktree; inspect before tagging.')
    require_main()
    committed = object_id('HEAD', 'Could not verify release commit.')
    parents = git('rev-list', '--parents', '--max-count=1', os.fsdecode(committed))
    if parents != committed + b' ' + parent + b'\n':
        raise ReleaseError('Release commit does not have the expected parent; inspect before tagging.')
    changed = git('diff-tree', '--no-commit-id', '--name-only', '-r', '-z', os.fsdecode(committed))
    if changed != os.fsencode(VERSION_FILE) + b'\0':
        raise ReleaseError('Release commit must change only bootstrap/version.txt; inspect before tagging.')
    if git('cat-file', 'blob', f'{os.fsdecode(committed)}:{VERSION_FILE}') != (target + '\n').encode('ascii'):
        raise ReleaseError('Release commit contains an unexpected version; inspect before tagging.')
    return os.fsdecode(committed)


def read_answer(prompt):
    print(prompt, end='', flush=True)
    answer = sys.stdin.readline()
    if not answer.endswith('\n'):
        raise EOFError
    return answer[:-1].strip(' \t')


def release(version, level):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ReleaseError('An interactive terminal (TTY) is required.')
    before = preflight()
    if level is None:
        level = read_answer(f'Current version: {version}\nChoose bump (major/minor/patch): ')
        if level not in LEVELS:
            raise ReleaseError('Choose major, minor or patch.')
    target = bump(version, level)
    require_new_tag(target)
    answer = read_answer(f'Current version: {version}\nBump: {level}\nTarget version: {target}\n\n'
                         'Proceed with release? [y/N]: ')
    if answer.lower() not in ('y', 'yes'):
        print('Cancelled; nothing changed.')
        return

    # The checkout, remote or tag may have changed while the operator was answering.
    if preflight() != before or read_version() != version:
        raise ReleaseError('Release inputs changed during confirmation; run the command again.')
    require_new_tag(target)

    try:
        write_version(target)
        print('Committing version bump...', flush=True)
        git('add', '--', str(VERSION_FILE), capture=False)
        git('commit', '-m', 'build: bump version', '--only', '--', str(VERSION_FILE), capture=False)
        committed = verify_commit(target, before[0])
        print(f'Creating annotated tag {target}...', flush=True)
        git('tag', '-a', target, '-m', target, committed, capture=False)
        tag_ref = f'refs/tags/{target}'
        error = 'Could not verify the annotated release tag.'
        tag_id = os.fsdecode(object_id(tag_ref, error))
        if (git('cat-file', '-t', tag_id) != b'tag\n'
                or object_id(f'{tag_id}^{{commit}}', error) != os.fsencode(committed)):
            raise ReleaseError(error)
        print(f'Pushing main and {target} atomically...', flush=True)
        git('-c', 'push.followTags=false', 'push', '--atomic', 'origin',
            f'{committed}:refs/heads/main', f'{tag_id}:{tag_ref}', capture=False)
    except (ReleaseError, OSError, KeyboardInterrupt):
        print(RECOVERY, file=sys.stderr)
        raise
    print(f'Pushed release tag {target}; GitHub Actions prepares a draft for manual publication.')


def main(args):
    level = None
    if args in (['--help'], ['-h']):
        print(USAGE)
        return 0
    if len(args) == 2 and args[0] == '--bump' and args[1] in LEVELS:
        level = args[1]
    elif args and args != ['--check']:
        print(USAGE, file=sys.stderr)
        return 2

    try:
        version = read_version()
        if args == ['--check']:
            check_version(version)
        else:
            release(version, level)
    except ReleaseError as error:
        print(f'Error: {error}', file=sys.stderr)
        return error.code
    except EOFError:
        print('Error: Input ended before release confirmation.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 1
    except OSError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    return 0


def interrupt(_number, _frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupt)
    sys.exit(main(sys.argv[1:]))
