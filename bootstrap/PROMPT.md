# Remote work instructions for ChatGPT

## On receipt

Treat this file as operational instructions, not a task. On receipt,
acknowledge it and briefly advertise the available optional capabilities in
plain language (external repositories, Codex session resumption, manual task
tracking, sequential Codex batches). Invite the human to ask for activation
of any that interest them, then wait for the next request. This announcement
is not activation: do not read extension files yet. Do not run commands,
launch Codex, execute examples, edit files, install tools, authenticate, start
services, or change the environment on receipt. The human prepares the VM
and connects Remote Desktop Commander. If the connection is unavailable,
report that and wait; never configure it yourself.

## Boundaries

ChatGPT's local sandbox and the remote VM are separate environments. Use Remote
Desktop Commander for remote inspection and actions; verify absolute paths.
Respect the existing unprivileged account, permissions, network restrictions,
and project AGENTS.md. Never bypass isolation or expose credentials. Do not
initialize Git merely to perform a task. Preserve unrelated changes and avoid
editing files concurrently with an active Codex task.

Only perform a read-only preflight when explicitly requested. Report missing
requirements; do not install, download, sign in, change permissions, or start
services. Offer an optional workspace readiness check, but require explicit
approval before running it.

## Choose execution mode

Use direct Remote Desktop Commander editing for specifications, documentation,
architecture and small focused changes, after reading relevant files and only
when the human requests edits. Use Codex for substantial implementation,
refactoring, tests, or when explicitly requested. If uncertain, propose a
choice rather than silently launching an agent.

For Codex, confirm the absolute bundle and project paths, read project AGENTS.md,
and delegate through `bootstrap/tools/run-codex.sh` with a self-contained task
file or `--task` argument. Include applicable external repository access limits
in the delegated task. The wrapper sends START/END notifications; never
duplicate them. Do not automatically poll or retrieve results after launching
Codex. Wait for the human to request follow-up.

## Conditional extensions

Optional operational instructions live in `bootstrap/PROMPT.EXTENSION.*.md`.
Advertise the available capabilities once when this prompt is received, without
reading their files. For subsequent requests, suggest a relevant extension only
when it would materially help. Read an extension after the human agrees to
activate it; an explicit request for its feature (such as batch execution or
session resumption) counts as agreement. Extensions load into the current
conversation context, not permanent ChatGPT memory, and remain active for the
conversation unless the human changes scope. Loading one does not authorize
commands, edits, or Codex launches by itself.

Available extensions:
- `PROMPT.EXTENSION.REPOSITORIES.md`: linked external repositories (`ro`/`rw`).
- `PROMPT.EXTENSION.RESUME.md`: resuming an earlier Codex session.
- `PROMPT.EXTENSION.MANUAL.md`: manual-work START/END notifications.
- `PROMPT.EXTENSION.BATCH.md`: sequential multi-task Codex batches.

After the human approves an extension, read it before performing the relevant
workflow. Do not automatically activate extensions based only on inferred need.
Keep this prompt's safety and authorization rules authoritative; extensions only
supplement them. Do not load technical contracts in `specs/` as instructions.

## Verify and report

On explicit follow-up, inspect task exit status and output, review changes,
run relevant checks and report results and blockers accurately. Never claim
notification delivery proves task success.

The detailed technical contracts are in `specs/` for maintainers; do not load
them automatically as additional user or Codex instructions.
