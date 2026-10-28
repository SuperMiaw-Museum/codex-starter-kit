# FreeBSD Shell Script Guidelines

## Purpose

Write small, maintainable FreeBSD administration scripts. Prefer native system utilities, straightforward control flow, and minimal dependencies over generic scripting frameworks.

## Runtime and conventions

- Target FreeBSD `/bin/sh` and POSIX shell syntax by default. Use another interpreter only for a concrete need, and make the requirement explicit.
- Start shell scripts with `#!/bin/sh` and `set -eu` by default. Deviate only when necessary and explain why.
- Handle expected command failures explicitly; do not disable `set -e` globally or suppress errors indiscriminately.
- Do not rely on `set -e` alone for error handling, especially in pipelines, functions, command substitutions, and cleanup traps. Use `set -o pipefail` when pipeline failures must be detected; FreeBSD `/bin/sh` supports it.
- Prefer FreeBSD-native commands and options; do not assume GNU-specific flags or Linux filesystem layouts.
- Keep scripts self-contained where practical. Introduce external dependencies only when justified and document them.
- Use descriptive snake_case names for new scripts, consistent with the repository.
- When creating or modifying shell scripts, prefer meaningful emoji prefixes on existing or newly authored human-facing status messages (✅ success, ⚠️ warning, ❌ error, ℹ️ information). Preserve machine-readable output, stdout data streams, stable output contracts, and script behavior; leave unrelated messages alone and avoid cosmetic churn. Retain plain text where cron or non-UTF-8 locales require it.

## Formatting and readability

- Use 4-space indentation and readable multiline blocks instead of compact one-liners.
- Separate logical stages with blank lines; comment non-obvious behavior rather than narrating every command.
- Quote variable expansions and handle paths, whitespace, and empty values deliberately.
- Use `shfmt` and ShellCheck as required development tools, not runtime dependencies.
- Format modified shell scripts with `shfmt -ln posix -i 4 -w` and verify formatting with `shfmt -ln posix -i 4 -d`.
- Avoid unrelated reformatting and preserve established conventions in existing scripts.

## Safety and operations

- Validate inputs, configuration, and required commands before making changes.
- Treat operations affecting filesystems, ZFS datasets, backups, permissions, services, and remote hosts as potentially destructive. Check preconditions and make failure paths explicit.
- Use traps and cleanup or restoration logic where partial failure could leave temporary resources or an inconsistent state.
- Never execute destructive ZFS operations, real backups, or remote changes in an isolated development sandbox. Test with mocks, temporary fixtures, and failure injection instead.
- Store local configuration under `conf/`; ignore its contents in Git except for `conf/sample/`.
- Keep sanitized, version-controlled configuration examples in `conf/sample/`, with no secrets or private environment-specific values. Ensure `.gitignore` rules explicitly allow tracking that directory and its files.
- Never invent, expose, or commit credentials or private configuration.

## Testing and documentation

- Run `shfmt -ln posix -i 4 -d`, `shellcheck -s sh`, and `sh -n` on modified shell scripts.
- Install missing development tools when permitted; otherwise report missing tools and checks not run.
- Add focused tests for meaningful behavior and regressions when practical, especially for failure handling and destructive operations.
- Keep tests in `tests/` and concise behavioral specifications and operational notes in `specs/`. Use these directories as standard project structure; do not create placeholder files or unnecessary documentation.
- Preserve existing behavior unless the task explicitly changes it. Make minimal, localized edits and report changes, checks, and limitations.
