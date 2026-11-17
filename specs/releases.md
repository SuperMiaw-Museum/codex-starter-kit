<!-- codex-starter-kit version: 1.0.0 -->

## Versions and releases

`bootstrap/version.txt` contains strict `a.b.c` plus a newline, without leading
zeroes or a `v` prefix. Tags use exactly that version as both annotated tag name
and message. The fixed watermark list is `release.sh`, `bootstrap/setup.sh`,
`bootstrap/remote-control.sh`, `README.md`, and `bootstrap/PROMPT.md`.
Shell markers use comments; Markdown markers use HTML comments. Other spec
watermarks are historical and are not release version files.

`./release.sh --check` validates each marker exactly once and requires it to
match version.txt. It is read-only and works without Git. In GitHub tag builds
(or `CI_COMMIT_TAG` context), the version must equal the tag and the tag must
point to HEAD. GitHub's tag context takes precedence. CI checks branches and tags.

## Local command

```bash
./release.sh                         # Ask for major, minor or patch
./release.sh --bump patch             # Or major / minor
./release.sh --help
```

There is no explicit version, --draft, --yes, --no-bump or publication mode.
Invalid arguments exit 2; help requires neither Git nor a terminal. A release
requires stdin and stdout terminals. Before prompting, validate all watermarks,
require a clean index and worktree (including untracked files), main tracking
origin/main, and HEAD equal to both the tracking ref and the live remote main.
Require configured author/committer identity and one identical origin fetch/push
URL. Do not fetch, pull, merge, rebase or change Git configuration.

When unspecified, ask `Choose bump (major/minor/patch):`; invalid input or EOF
stops without writes. Increment the selected component and reset lower components.
Components above nine digits are rejected for bump arithmetic to avoid overflow.
Check the exact target tag locally and remotely before confirmation; failures in
remote lookup stop the operation. Never move, delete or overwrite existing tags.
An existing current-version tag is allowed: the target must be new.

Display current version, bump and target, then ask
`Proceed with release? [y/N]: `. Only y/yes, case-insensitive with surrounding
whitespace ignored, confirms. Declining exits 0 and changes nothing. EOF or an
interruption fails. There is no default bump and no automatic confirmation.

On confirmation synchronize the six version files, explicitly stage only those
paths, commit `build: bump version`, recheck versions and clean status, then run:

```bash
git tag -a VERSION -m VERSION
git -c push.followTags=false push --atomic origin main VERSION
```

Native commit/tag/push output is preserved. Disabling followTags prevents Git
configuration from adding unrelated tags. No build, install, GitHub API call or
asset upload occurs locally. Failures stop subsequent steps without rollback,
force or automatic retry. Inspect git status, git log -1, git tag and origin
before manual recovery. Commit/tag failures may leave local changes or commits;
failed atomic pushes retain local commits/tags but publish neither requested ref.
Concurrent remote changes, hooks and authentication can still reject publication.
No atomic-push fallback is allowed.

## GitHub draft and publishing

The tag-only draft job depends on successful checks. GitHub Actions owns release
creation and any future builds/assets. No build or asset pipeline currently exists.
Workflow concurrency serializes runs for each tag without cancellation. The job
uses `gh api --paginate` to query all release pages (including drafts); an existing
draft or public release is left unchanged. Lookup failure prevents creation.
Otherwise the workflow uses `gh release create` with `--draft`, the tag as title,
and generated notes, authenticated with `GH_TOKEN` from `secrets.GITHUB_TOKEN`.
It never publishes automatically or changes tags.
Independent API writers must coordinate to avoid racing draft creation.

Review and publish the existing draft manually in GitHub Releases, or explicitly
run `gh release edit VERSION --draft=false` against the intended repository.
Do not create a second release to publish it.

Tests use isolated temporary repositories and bare remotes only. Never initialize
Git, commit, tag or push in the source workspace to validate this command.
