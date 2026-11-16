<!-- codex-starter-kit version: 1.0.0 -->

## Task runner and notifications

```bash
bootstrap/tools/run-codex.sh /absolute/project /absolute/task.txt
bootstrap/tools/run-codex.sh /absolute/project --task "Read AGENTS.md and run the relevant checks."
token=$(bootstrap/tools/manual-task.sh start project 'Requested manual work')
# Perform the requested work and capture its status immediately.
result=$?
bootstrap/tools/manual-task.sh end "$token" "$result"
bootstrap/tools/manual-task.sh status
```

The runner reads task instructions through stdin, loads NVM if present, uses the
configured Codex permissions, and reports `SANDBOX_HANDOFF_EXIT_CODE=N` on exit.
The explicit `--task` form replaces the old positional inline text interface.

All checkouts share a private per-user registry at
`/tmp/codex-starter-kit-tasks-UID` (Bash, Python 3 and Linux `/proc` required).
Random tokens identify tasks; `flock` and atomic replacement protect updates.
The lock also serializes delivery, so concurrent notifications reflect each
transition in order. Counts include both manual and Codex tasks and always show
zero, with the count immediately before the status emoji:

```text
[12:00:00] 🤖 3 🚀 START Codex [project] — label
[12:00:42] 🤖 2 ✅ END Codex [project] (42s)
[12:00:44] 🤖 1 ❌ ERROR Codex [project] (18s)
[12:01:35] 🤖 0 ✅ END Codex [project] (95s)
```

Manual notices use `Manual`. START writes its notice to stderr and only the token
to stdout; END writes a plain notice to stdout. Sanitized, bounded notices also
go to same-user PTYs with owner checks, no symlink following, color and an END bell.
Keep tokens to finish tasks across remote sessions. Duplicate/unknown END returns
3 without removing another task; invalid input or registry failures return 2.
Failed state writes preserve tasks for retry. Same-user processes are trusted.
Malformed registry entries reject a transition before any state write or notice;
project names and labels are sanitized again when read. Do not edit registry files
to adjust the count: it is derived from task identities, never stored separately.

Read-only `status` lists active tokens and stale counts. Dead Codex runners are
recognized by PID, process start identity and boot ID; manual tasks expire after
24 hours. Stale entries are excluded from status and pruned during the next
successful transition. There is no automatic monitor. A valid END can still
finish a stale entry before pruning. Keep manual work within 24 hours and do not
remove registry files while tasks run. Crashes after an atomic state commit may
lose a notification, and SIGKILL cannot run the runner's END trap. Terminal
delivery is best effort; use task output and exit status to assess success.

Run the offline checks with `python3 -m unittest discover -s tests -v`. They use
private temporary registries and fake Codex commands, exercise concurrent mixed
START/END delivery and failed END retries, and never broadcast to user terminals
or operate the live service.
