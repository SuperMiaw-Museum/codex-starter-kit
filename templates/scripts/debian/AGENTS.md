# Debian Bash Script Guidelines

## Purpose

Write small, maintainable Debian administration scripts. Prefer native system utilities, straightforward control flow, and minimal dependencies over generic scripting frameworks.

## Runtime and conventions

- Use Bash as the standard interpreter, with `#!/usr/bin/env bash`. Do not restrict new scripts to POSIX `sh` without a project-specific reason.
- Start scripts with `set -euo pipefail` by default. Deviate only when necessary and explain why.
- Handle expected command failures explicitly; do not disable `set -e` globally or suppress errors indiscriminately.
- Do not rely on `set -e` or `pipefail` alone for error handling, especially in conditional contexts, functions, command substitutions, and cleanup traps.
- Prefer Debian-native tools and GNU/Linux conventions; do not assume FreeBSD command flags or filesystem layouts.
- Keep scripts self-contained where practical. Introduce external dependencies only when justified and document them.
- Use descriptive snake_case names for new scripts, consistent with the repository.
- Allow Fish shell helpers for interactive workflows (functions, aliases, and completions) when useful. Keep administration scripts in Bash; Fish helpers should invoke them rather than duplicate their logic.
- Generate Fish helpers only when the task or project requires them. Do not assume Fish is installed on the target system.
- When Fish helpers are provided, include a bootstrap script to install or update them in the appropriate user-level Fish directories (such as `~/.config/fish/functions/` and `~/.config/fish/completions/`).
- Keep the Fish bootstrap idempotent, avoid unnecessary edits to `config.fish`, and never silently overwrite unrelated user files. Document how to run it and how to remove installed helpers.

## Formatting and readability

- Use 4-space indentation and readable multiline blocks instead of compact one-liners.
- Separate logical stages with blank lines; comment non-obvious behavior rather than narrating every command.
- Quote variable expansions and handle paths, whitespace, and empty values deliberately.
- Use `shfmt` and ShellCheck as required development tools, not runtime dependencies.
- Format modified Bash scripts with `shfmt -ln bash -i 4 -w` and verify formatting with `shfmt -ln bash -i 4 -d`.
- Avoid unrelated reformatting and preserve established conventions in existing scripts.

## Safety and operations

- Validate inputs, configuration, and required commands before making changes.
- Treat operations affecting filesystems, backups, permissions, packages, services, and remote hosts as potentially destructive. Check preconditions and make failure paths explicit.
- Use traps and cleanup or restoration logic where partial failure could leave temporary resources or an inconsistent state.
- Never execute destructive system operations, real backups, or remote changes in an isolated development sandbox. Test with mocks, temporary fixtures, and failure injection instead.
- Store local configuration under `conf/`; ignore its contents in Git except for `conf/sample/`.
- Keep sanitized, version-controlled configuration examples in `conf/sample/`, with no secrets or private environment-specific values. Ensure `.gitignore` rules explicitly allow tracking that directory and its files.
- Never invent, expose, or commit credentials or private configuration.

## Testing and documentation

- Run `shfmt -ln bash -i 4 -d`, `shellcheck -s bash`, and `bash -n` on modified Bash scripts.
- Install missing development tools when permitted; otherwise report missing tools and checks not run.
- Add focused tests for meaningful behavior and regressions when practical, especially for failure handling and destructive operations.
- Keep tests in `tests/` and concise behavioral specifications and operational notes in `specs/`. Use these directories as standard project structure; do not create placeholder files or unnecessary documentation.
- Preserve existing behavior unless the task explicitly changes it. Make minimal, localized edits and report changes, checks, and limitations.
