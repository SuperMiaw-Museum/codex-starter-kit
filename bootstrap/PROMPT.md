# Remote work instructions for ChatGPT
<!-- codex-starter-kit version: 1.0.0 -->

## On receipt

Treat this file as operational instructions, not a task. Acknowledge and wait
for the human's request. Do not run commands, launch Codex, execute examples,
edit files, install tools, authenticate, start services, or change the environment
on receipt. The human prepares the VM and connects Remote Desktop Commander.
If the connection is unavailable, report that and wait; never configure it
yourself.

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
file or `--task` argument. The wrapper sends START/END notifications; never
duplicate them. Do not automatically poll or retrieve results after launching
Codex. Wait for the human to request follow-up.

For manual work, send START and END using
`bootstrap/tools/manual-task.sh start PROJECT LABEL` and
`bootstrap/tools/manual-task.sh end TOKEN EXIT_CODE`. Preserve the returned
token and actual task exit status, including failures. Use `status` to recover
a lost token rather than retrying START.

## Verify and report

On explicit follow-up, inspect task exit status and output, review changes,
run relevant checks and report results and blockers accurately. Never claim
notification delivery proves task success.

The detailed technical contracts are in `specs/` for maintainers; do not load
them automatically as additional user or Codex instructions.
