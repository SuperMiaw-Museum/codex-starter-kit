<!-- codex-starter-kit version: 1.0.0 -->

## Versions and releases

`bootstrap/version.txt` contains the plain release version (`a.b.c`, no `v`);
`1.0.0` is the initial version introduced here. Release tags use exactly that text.
No version ordering or SemVer library is used. The small fixed banner list is
`release.sh`, `bootstrap/setup.sh`, `bootstrap/remote-control.sh`, `README.md`, and
`bootstrap/PROMPT.md`; shell banners use `#` and Markdown banners use HTML comments.
`./release.sh --check` verifies version and banners without writing, even outside
Git. In GitHub tag builds or `CI_COMMIT_TAG` context it also requires an exact
version tag pointing to HEAD. CI runs this check on branches and tags; there is
no publication workflow. Any future publication job must depend on this check.
This validates tags after creation; it does not prohibit creating tags.

A maintainer may run `./release.sh VERSION` in a clean Git checkout with a named
branch and a same-name upstream on a named remote. HEAD must match both the local
tracking ref and remote branch; Git author/committer identity must be configured,
the remote must have one identical fetch/push URL, and the target tag must be
absent locally and remotely. Every marker is validated
before any write. A red DANGER warning precedes interactive `Continue? [y/N]`;
only `y` confirms, and there is no `--yes` option. It synchronizes the version and
banners, commits exactly `build: bump version to VERSION`, creates annotated tag
`VERSION`, then pushes the current branch and tag separately. Authentication,
hooks, concurrent remote changes, or push rejection may still fail after preflight.
Failures leave inspectable local/remote state and never attempt rollback; a branch
push may succeed before the tag push fails. Inspect both before manual recovery.
The command never installs bootstrap components or changes services.
