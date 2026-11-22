# Repository maintenance

This root AGENTS.md is authoritative for maintenance of codex-starter-kit.

- Files under `templates/`, including every nested `AGENTS.md`, are data/templates
  to maintain, not instructions governing this repository. Copy or adapt them for
  destination projects; do not apply their contents to repository maintenance.
- Keep project templates in flat, named directories under `templates/` and
  `bootstrap/` for the Codex remote workflow. Keep documentation and paths aligned.
- Keep human bootstrap commands at the root of `bootstrap/`, ChatGPT/Codex
  commands and prompt extensions in `bootstrap/orchestration/`, and their
  implementation in `bootstrap/internal/`.
- Do not modify `README.md` or files under `docs/` unless explicitly requested,
  except `docs/SCRATCHPAD.md`, which may be created, edited, cleared, or deleted
  as needed.
- Preserve simple, portable Bash scripts and their executable permissions. Verify
  all bootstrap shell files with `bash -n` after changes.
- Work in the existing repository directory. Do not rename it, initialize Git,
  modify installed home-directory scripts, or deploy unless explicitly requested.

# Documentation

- Keep `README.md` concise as the project entry point. Put user-facing details
  in `docs/` when they warrant a separate page, technical contracts in
  `specs/`, and standing rules for agents in this `AGENTS.md`.
- Do not change official documentation as a side effect of implementation.
  Record potential additions, corrections, or removals in `docs/SCRATCHPAD.md`
  instead. Keep observations concise and contextual; create the file only when
  needed. Review them separately when requested, then clear or delete the file.
  The scratchpad is temporary, not a journal or backlog.

# Language policy

- Use the user's language for conversation, specifications, and reports.
- Use English by default for code, comments, documentation, logs, and CLI output,
  unless the user explicitly requests an exception.
- Specify UI language per project. Never infer it from the user's language or
  source code; ask when it is unspecified.
