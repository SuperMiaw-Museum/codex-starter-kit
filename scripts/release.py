#!/usr/bin/env python3
"""Small explicit release transaction; --check is read-only and works without Git."""
from pathlib import Path
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
# Deliberately small, fixed list. Each file uses its native comment syntax.
BANNERS = {
    'release.sh': '# codex-starter-kit version: {version}',
    'bootstrap/setup.sh': '# codex-starter-kit version: {version}',
    'bootstrap/remote-control.sh': '# codex-starter-kit version: {version}',
    'README.md': '<!-- codex-starter-kit version: {version} -->',
    'bootstrap/PROMPT.md': '<!-- codex-starter-kit version: {version} -->',
}
SEMVER = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def transform(version):
    """Validate every marker and return synchronized content, without writing."""
    result = {'bootstrap/version.txt': version + '\n'}
    for name, template in BANNERS.items():
        content = (ROOT / name).read_text()
        pattern = re.escape(template).replace(re.escape('{version}'), SEMVER)
        matches = list(re.finditer('^' + pattern + '$', content, re.MULTILINE))
        if len(matches) != 1:
            raise ValueError(f'{name}: expected exactly one valid version banner')
        match = matches[0]
        result[name] = content[:match.start()] + template.format(version=version) + content[match.end():]
    return result


def main():
    if len(sys.argv) != 2:
        raise ValueError('Usage: ./release.sh VERSION | --check')
    current = (ROOT / 'bootstrap/version.txt').read_text()
    if not re.fullmatch(SEMVER + '\n', current):
        raise ValueError('version.txt must contain plain a.b.c and a newline')
    current = current.strip()
    check = sys.argv[1] == '--check'
    version = current if check else sys.argv[1]
    if not re.fullmatch(SEMVER, version):
        raise ValueError('VERSION must be plain a.b.c without a v prefix')
    updated = transform(version)
    if check:
        for name, content in updated.items():
            if (ROOT / name).read_text() != content:
                raise ValueError(f'{name}: version differs from version.txt')
        tag = os.environ.get('CI_COMMIT_TAG', '')
        ref = os.environ.get('GITHUB_REF', '')
        if ref.startswith('refs/tags/'):
            tag = ref[len('refs/tags/'):]
        if tag:
            if tag != version or git('rev-parse', f'refs/tags/{tag}^{{commit}}') != git('rev-parse', 'HEAD'):
                raise ValueError('release tag must equal version.txt and point to HEAD')
        print(f'Version banners consistent: {version}')
        return
    # All preconditions and rendering precede the prompt and any modification.
    if Path(git('rev-parse', '--show-toplevel')).resolve() != ROOT:
        raise ValueError('Release must run at this repository root')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Release requires a clean worktree, including untracked files')
    branch = git('symbolic-ref', '--quiet', '--short', 'HEAD')
    upstream = git('rev-parse', '--abbrev-ref', '@{upstream}')
    remote = git('config', f'branch.{branch}.remote')
    merge = git('config', f'branch.{branch}.merge')
    if remote == '.' or merge != f'refs/heads/{branch}' or upstream != f'{remote}/{branch}':
        raise ValueError('Require a same-name branch upstream on a named remote')
    fetch_url = git('remote', 'get-url', '--all', remote)
    push_url = git('remote', 'get-url', '--push', '--all', remote)
    if '\n' in fetch_url or fetch_url != push_url:
        raise ValueError('Require one identical fetch/push URL so preflight checks the push destination')
    if git('rev-parse', 'HEAD') != git('rev-parse', '@{upstream}'):
        raise ValueError('HEAD must match its upstream tracking ref')
    remote_head = git('ls-remote', '--exit-code', remote, merge).split()[0]
    if remote_head != git('rev-parse', 'HEAD'):
        raise ValueError('Remote branch changed; synchronize before releasing')
    git('var', 'GIT_AUTHOR_IDENT')
    git('var', 'GIT_COMMITTER_IDENT')
    if git('tag', '--list', version) or git('ls-remote', '--tags', remote, f'refs/tags/{version}'):
        raise ValueError('Release tag already exists locally or remotely')
    if version == current:
        raise ValueError('Choose a different release version (versions are compared literally)')
    if not sys.stdin.isatty():
        raise ValueError('Release requires an interactive terminal; no automatic confirmation')
    print(f'\033[1;31mDANGER: will modify files, commit, create annotated tag {version}, '
          f'and push {branch} and {version} to {remote}.\033[0m', flush=True)
    if input('Continue? [y/N] ').strip().lower() != 'y':
        print('Cancelled; nothing changed.')
        return
    for name, content in updated.items():
        (ROOT / name).write_text(content)
    git('add', '--', *updated)
    git('commit', '-m', f'build: bump version to {version}')
    git('tag', '-a', version, '-m', version)
    # Separate pushes are intentional: report partial failure; never force or roll back.
    git('push', '--no-follow-tags', remote, f'HEAD:{merge}')
    git('push', '--no-follow-tags', remote, f'refs/tags/{version}:refs/tags/{version}')
    print(f'Released {version}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, EOFError, KeyboardInterrupt) as error:
        print(f'Release failed: {error}. No rollback attempted; inspect local and remote state before retrying.', file=sys.stderr)
        sys.exit(1)
