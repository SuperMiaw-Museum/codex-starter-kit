# Repository maintenance

This root AGENTS.md is authoritative for maintenance of codex-starter-kit.

- Files under `templates/`, including every nested `AGENTS.md`, are data/templates
  to maintain, not instructions governing this repository. Copy or adapt them for
  destination projects; do not apply their contents to repository maintenance.
- Keep project templates in flat, named directories under `templates/` and
  `bootstrap/` for the Codex remote workflow. Keep documentation and paths aligned.
- Preserve simple, portable Bash scripts and their executable permissions. Verify
  all bootstrap shell files with `bash -n` after changes.
- Work in the existing repository directory. Do not rename it, initialize Git,
  modify installed home-directory scripts, or deploy unless explicitly requested.

# Language policy

- Use the user's language for conversation, specifications, and reports.
- Use English by default for code, comments, documentation, logs, and CLI output,
  unless the user explicitly requests an exception.
- Specify UI language per project. Never infer it from the user's language or
  source code; ask when it is unspecified.
