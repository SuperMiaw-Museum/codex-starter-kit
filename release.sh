#!/usr/bin/env bash
# codex-starter-kit version: 2.2.0
set -Eeuo pipefail

usage() {
    cat <<'HELP'
Usage: ./release.sh [--bump major|minor|patch] | --check | --help

Choose a bump interactively when omitted. Confirm once (default: no), update
version banners, commit, create an annotated X.Y.Z tag, and atomically push
main and the tag to origin. Requires a clean checkout on main, synchronized
with origin/main, and an interactive terminal. No automatic rollback.
--check validates versions without writing; it also checks CI tag context.
GitHub Actions prepares a draft release for manual publication.
HELP
}
fail() { echo "Error: $*" >&2; exit 1; }
level= check=false
case $# in
    0) ;;
    1) case $1 in
        --help|-h) usage; exit 0 ;;
        --check) check=true ;;
        *) usage >&2; exit 2 ;;
    esac ;;
    2) [[ $1 == --bump && $2 =~ ^(major|minor|patch)$ ]] || { usage >&2; exit 2; }; level=$2 ;;
    *) usage >&2; exit 2 ;;
esac
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
trap 'echo "No rollback performed. Inspect git status, git log -1 and git tag before retrying; after a failed push, check origin. Local changes, commits or tags may remain." >&2' ERR
trap 'exit 1' INT TERM
cd "$root"
files=(release.sh bootstrap/setup.sh bootstrap/remote-control.sh README.md bootstrap/PROMPT.md)
version=$(cat bootstrap/version.txt)
semver='(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
[[ $version =~ ^$semver$ && $(wc -l < bootstrap/version.txt) -eq 1 && $(wc -c < bootstrap/version.txt) -eq $((${#version} + 1)) ]] || fail 'version.txt must contain plain a.b.c and a newline'
for file in "${files[@]}"; do
    case $file in
        *.sh) banner="# codex-starter-kit version: $version"; pattern="^# codex-starter-kit version: $semver$" ;;
        *) banner="<!-- codex-starter-kit version: $version -->"; pattern="^<!-- codex-starter-kit version: $semver -->$" ;;
    esac
    [[ $(grep -Ec "$pattern" "$file") == 1 && $(grep -Fxc "$banner" "$file") == 1 ]] || fail "$file: expected exactly one valid banner matching version.txt"
done
if $check; then
    tag=${CI_COMMIT_TAG:-}
    [[ ${GITHUB_REF:-} != refs/tags/* ]] || tag=${GITHUB_REF#refs/tags/}
    if [[ -n $tag ]]; then
        [[ $tag == "$version" ]] || fail 'release tag must equal version.txt'
        [[ $(git rev-parse "refs/tags/$tag^{commit}") == "$(git rev-parse HEAD)" ]] || fail 'release tag must point to HEAD'
    fi
    echo "Version banners consistent: $version"
    exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'An interactive terminal (TTY) is required.'
[[ $(git rev-parse --show-toplevel) == "$root" ]] || fail 'Run from this Git repository root.'
[[ $(git status --porcelain --untracked-files=all) == '' ]] || fail 'Release requires a clean worktree, including untracked files.'
[[ $(git symbolic-ref --quiet --short HEAD) == main ]] || fail 'Current branch must be main.'
[[ $(git config branch.main.remote) == origin && $(git config branch.main.merge) == refs/heads/main && $(git rev-parse --abbrev-ref '@{upstream}') == origin/main ]] || fail 'Require main tracking origin/main.'
fetch_url=$(git remote get-url --all origin)
push_url=$(git remote get-url --push --all origin)
[[ -n $fetch_url && $fetch_url != *$'\n'* && $fetch_url == "$push_url" ]] || fail 'Require one identical fetch/push URL.'
head=$(git rev-parse HEAD)
[[ $head == "$(git rev-parse refs/remotes/origin/main)" ]] || fail 'HEAD must match origin/main exactly; synchronize manually.'
remote_head=$(git ls-remote --exit-code origin refs/heads/main)
[[ ${remote_head%%[[:space:]]*} == "$head" ]] || fail 'Remote main changed; synchronize manually.'
git var GIT_AUTHOR_IDENT >/dev/null
git var GIT_COMMITTER_IDENT >/dev/null
if [[ -z $level ]]; then
    printf 'Current version: %s\nChoose bump (major/minor/patch): ' "$version"
    read -r level
    [[ $level =~ ^(major|minor|patch)$ ]] || fail 'Choose major, minor or patch.'
fi
IFS=. read -r major minor patch <<< "$version"
# Bound arithmetic to avoid overflow; larger versions remain valid for --check.
for part in "$major" "$minor" "$patch"; do
    [[ ${#part} -le 9 ]] || fail 'Version components are too large to bump safely.'
done
case $level in
    major) target="$((major + 1)).0.0" ;;
    minor) target="$major.$((minor + 1)).0" ;;
    patch) target="$major.$minor.$((patch + 1))" ;;
esac
[[ -z $(git tag --list "$target") ]] || fail "Target tag already exists: $target"
remote_tag=$(git ls-remote --tags origin "refs/tags/$target")
[[ -z $remote_tag ]] || fail "Target tag already exists on origin: $target"
printf 'Current version: %s\nBump: %s\nTarget version: %s\n\nProceed with release? [y/N]: ' "$version" "$level" "$target"
read -r answer
case $answer in
    [yY]|[yY][eE][sS]) ;;
    *) echo 'Cancelled; nothing changed.'; exit 0 ;;
esac
perform_release() {
    temp=$(mktemp -d)
    trap 'rm -rf -- "$temp"' EXIT
    for file in "${files[@]}"; do
        awk -v old="$version" -v new="$target" '
            $0 == "# codex-starter-kit version: " old { $0 = "# codex-starter-kit version: " new }
            $0 == "<!-- codex-starter-kit version: " old " -->" { $0 = "<!-- codex-starter-kit version: " new " -->" }
            { print }
        ' "$file" > "$temp/rendered"
        cat "$temp/rendered" > "$file"
    done
    printf '%s\n' "$target" > bootstrap/version.txt
    echo 'Committing version bump...'
    git add -- bootstrap/version.txt "${files[@]}"
    git commit -m 'build: bump version'
    # Hooks must not silently change release content or leave unexpected files.
    ./release.sh --check
    [[ $(cat bootstrap/version.txt) == "$target" ]] || fail 'Commit changed the target version; inspect before tagging.'
    [[ -z $(git status --porcelain --untracked-files=all) ]] || fail 'Commit left a dirty worktree; inspect before tagging.'
    echo "Creating annotated tag $target..."
    git tag -a "$target" -m "$target"
    echo "Pushing main and $target atomically..."
    git -c push.followTags=false push --atomic origin main "$target"
    echo "Pushed release tag $target; GitHub Actions prepares a draft for manual publication."
}
perform_release
