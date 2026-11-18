# Legacy Project Guidelines

## Purpose

Gradually make this hand-written legacy project easier for humans and coding agents to maintain, through small, task-driven changes. Preserve its established architecture and behavior. Do not modernize it by default.

## Language policy

- Use the user's language for conversation, specifications, and reports.
- Use English by default for code, comments, documentation, logs, and CLI output, unless the user explicitly requests an exception.
- Specify UI language per project. Never infer it from the user's language or source code; ask when it is unspecified.

## Scope and implementation

- Make the smallest change that fully addresses the requested task.
- Follow existing architecture, naming, style, algorithms, and project conventions, even when alternatives seem cleaner.
- Reuse existing functions, utilities, and patterns before adding new ones.
- Do not refactor, reorganize, reformat, or modernize unrelated code.
- Do not introduce dependencies, frameworks, layers, abstractions, or speculative defensive mechanisms without explicit approval.
- Preserve existing behavior and backward compatibility unless the task explicitly changes them.
- Keep diffs localized and proportional to the request. Necessary changes across multiple files are acceptable.
- Report unrelated problems rather than fixing them without authorization.
- If a larger architectural change seems necessary, stop and explain the trade-offs before implementing it.

## Tests

- Add focused tests in `tests/` for meaningful new behavior and relevant bug regressions when reasonably feasible.
- Reuse the project's existing test framework and conventions, if any.
- Do not retroactively cover unrelated legacy code or restructure production code just to make it testable.
- **If a new or different testing framework is needed, STOP before installing, configuring, or using it.** Explain the limitations and alternatives, and ask the user to discuss the choice with ChatGPT before proceeding.
- If testing cannot reasonably proceed without that decision, report this limitation; do not silently select a framework.

## Specifications and documentation

- Document significant new or changed behavior in concise Markdown files under `specs/`.
- Update an existing relevant spec instead of duplicating it.
- Describe observable behavior, intent, constraints, and important edge cases, not a line-by-line implementation.
- Do not launch retroactive documentation or reverse-engineering work unrelated to the current task.
- Skip new specs for trivial or purely internal changes.
- Keep `README.md` concise as the project entry point. Follow established documentation conventions and use `docs/` for details that warrant a separate page.
- Do not modify `README.md` or files under `docs/` unless explicitly requested,
  except `docs/SCRATCHPAD.md`, which may be created, edited, cleared, or deleted
  as needed to note potential documentation additions, corrections, or removals.
- Record project-specific invariants in the project's own `AGENTS.md`.
- Keep scratchpad notes short and contextual, with no assigned owner or review
  schedule. Review them separately when requested, then clear or delete them;
  the scratchpad is not a permanent journal or backlog.

## Workflow

- Before editing, inspect only the relevant code and identify the smallest viable solution.
- Ask for clarification when ambiguity materially affects behavior or the work would exceed the agreed scope.
- Run relevant existing checks where available; do not claim success for checks that were not run.
- Review the final diff for accidental, unrelated, or unnecessary changes.
- Summarize changes, tests performed, and any limitations or decisions left open.

The goal is an increasingly documented and testable codebase, **not** a rewritten one. Optimize for long-term human readability, not dependence on a particular agent.
