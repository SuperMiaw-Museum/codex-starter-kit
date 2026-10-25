# Repository maintenance

This root AGENTS.md is authoritative for maintenance of codex-starter-kit.

- Files under `templates/`, including every nested `AGENTS.md`, are data/templates
  to maintain, not instructions governing this repository. Copy or adapt them for
  destination projects; do not apply their contents to repository maintenance.
- Keep `templates/apps/` and `templates/scripts/` for project templates and
  `bootstrap/` for the Codex remote workflow. Keep documentation and paths aligned.
- Preserve simple, portable Bash scripts and their executable permissions. Verify
  all bootstrap shell files with `bash -n` after changes.
- Work in the existing repository directory. Do not rename it, initialize Git,
  modify installed home-directory scripts, or deploy unless explicitly requested.
