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
[12:00:00] 🤖 3 🚀 START Codex [project] task preview | task: 8e5940bf314a
[12:00:42] 🤖 2 ✅ END Codex [project] duration: 42s | task: 8e5940bf314a | model: gpt-5.4 | tokens: 1,025 | session: 01234567-89ab-4def-8123-0123456789ab
[12:00:44] 🤖 1 ❌ ERROR Codex [project] duration: 18s | task: f260a31cd2b4
[12:01:35] 🤖 0 ✅ END Codex [project] duration: 95s | task: 50d6f124a3e7
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

Codex START, END and ERROR summaries show `task:` followed by the first 12
hexadecimal characters of the registry token. A START with `--resume` ends
with `resume:` and the requested UUID. Codex END and ERROR summaries show the
duration, the task identifier, and any available `model:`, `tokens:` and
`session:` fields, in that order, with ` | ` between fields. The `session:`
field comes from the session reported by Codex. By default
the runner uses `codex exec --json` when supported and streams completed agent messages and
errors. Set `CODEX_STARTER_KIT_NOTIFY_METADATA=0` to disable capture and use the
usual Codex tool and progress display. The token count
adds input and output from the completed turn, with cached input already
included. The model comes from the matching local session record. Failed or
interrupted runs can have partial metadata. Unavailable fields are omitted.

Set `CODEX_STARTER_KIT_LIMIT_REMINDER=1` to append a quota reminder to Codex
END and ERROR notices on stdout and the same terminals. Only the literal
value `1` enables it. The reminder shows the age of the newest trustworthy
snapshot found, for example `Usage snapshot: 2m ago`. Snapshots at least
15 minutes old are marked stale. A snapshot is marked expired as soon as
either quota window has reset. Stale and expired reminders show only the
snapshot age and status. Fresh reminders show both remaining percentages and
reset countdowns rounded up to whole minutes. If neither lookup nor cache
yields a trustworthy snapshot, the notice shows `Codex usage: unavailable`.

Lookup checks bounded tails of local session logs in `$CODEX_HOME/sessions/`
(default `~/.codex/sessions/`), favoring recent directories and recently
written files. It checks at most 24 directories, 1,024 directory entries,
and 24 file tails of up to 256 KiB each, with a one-second scan budget.
It accepts complete token-count records with a nonfuture timestamp, both
expected window lengths, valid percentages, and resets within the respective
windows measured from the snapshot timestamp. A held-over snapshot is
revalidated every time it is displayed.

The private task registry keeps one atomically replaced cache entry scoped
to the last Codex home recorded there. Cache writes share the existing
registry lock, and malformed or unsafe cache files are ignored. Bounded
lookup can miss snapshots in older directories, before the file tails, or
outside the scan budget. Quota lookup and cache failures are best effort and
do not change task tracking or notification delivery.

Run the offline checks with `python3 -m unittest discover -s tests -v`. They use
private temporary registries and fake Codex commands, exercise concurrent mixed
START/END delivery and failed END retries, and never broadcast to user terminals
or operate the live service.

## Live inspection

With `CODEX_STARTER_KIT_LIVE_INSPECT=1`, each runner launches Codex in a
dedicated tmux session and prints `SANDBOX_HANDOFF_TMUX_SESSION` and a
`tmux -S … attach -r -t …` command on stdout. Run that command on the same
host as the runner and use tmux copy mode for recent scrollback. The pane
displays Codex stderr and, in JSON mode, agent messages, errors, and tool
command and output events. The runner still reports START and END, the
Codex exit code, and the Codex session UUID when captured. Use that UUID
for `--resume`.

The session uses tmux's default bounded scrollback and closes when the
mission ends. The runner cleans up the dedicated server and temporary
files on exit, including on handled signals. The pane has no interactive
shell after Codex completes. tmux must be installed for this opt-in mode.
