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
[12:00:00] 🤖 3 🚀 START Codex [project] task preview
[12:00:42] 🤖 2 ✅ END Codex [project] duration: 42s | model: gpt-5.4 | tokens: 1,025
[12:00:44] 🤖 1 ❌ ERROR Codex [project] duration: 18s
[12:01:35] 🤖 0 ✅ END Codex [project] duration: 95s
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

Read-only `status` shows active and stale counts. Each active task uses two lines:
`YYYY-MM-DD HH:MM Codex [project]` or `YYYY-MM-DD HH:MM Manual [project]` in local
time, followed by its label indented by two spaces. The label uses up to 120
characters with `...` appended when truncated. Status uses the same label
whether or not full START prompts are enabled. Dead Codex runners are
recognized by PID, process start identity and boot ID; manual tasks expire after
24 hours. Stale entries are excluded from status and pruned during the next
successful transition. There is no automatic monitor. A valid END can still
finish a stale entry before pruning. Keep manual work within 24 hours and do not
remove registry files while tasks run. Crashes after an atomic state commit may
lose a notification, and SIGKILL cannot run the runner's END trap. Terminal
delivery is best effort; use task output and exit status to assess success.

By default, Manual and Codex START show a single-line task preview of up to 120
characters. A truncated preview adds `...` after those characters.
Set `CODEX_STARTER_KIT_SHOW_FULL_PROMPT=1` in the task's environment to show
only the START header on the first line and the full task prompt below it in
the SSH terminal. The prompt keeps its line breaks and backslashes and is
written as plain text. The registry stores only the bounded label.

Codex END and ERROR notices show the duration followed by any available
`model:`, `tokens:` and `session:` fields, with ` | ` between fields. By default
the runner uses `codex exec --json` when supported and streams completed agent messages and
errors. Set `CODEX_STARTER_KIT_NOTIFY_METADATA=0` to disable capture and use the
usual Codex tool and progress display. The token count
adds input and output from the completed turn, with cached input already
included. The model comes from the matching local session record. Failed or
interrupted runs can have partial metadata. Unavailable fields are omitted.

Set `CODEX_STARTER_KIT_LIMIT_REMINDER=1` to append two quota lines to Codex
END and ERROR notices on stdout and the same terminals. Only the literal
value `1` enables the reminder. It uses the newest valid local token-count
snapshot under `~/.codex/sessions/` that is at most five hours old and has
future resets for both the 5-hour and weekly windows. Countdown values round
up to whole minutes. If the snapshot is unavailable or the scan exceeds its
one-second budget, delivery continues without the block.

Run the offline checks with `python3 -m unittest discover -s tests -v`. They use
private temporary registries and fake Codex commands, exercise concurrent mixed
START/END delivery and failed END retries, and never broadcast to user terminals
or operate the live service.
