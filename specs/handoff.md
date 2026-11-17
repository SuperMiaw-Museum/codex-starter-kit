# Handoff contract and original operational reference

This reference preserves the pre-refactor operational instructions for review.
The active ChatGPT contract remains in `bootstrap/PROMPT.md`.

## On receipt: acknowledge and wait

Treat this prompt as instructions, not a task. Acknowledge it and wait for the
human's request. Do not run commands, launch Codex, execute examples, edit files,
install tools, authenticate, start services, or change the environment on receipt.

The human explicitly runs `bootstrap/setup.sh --install-hook` and/or
`--install-service` if wanted; do not run setup on receipt of this prompt.
`remote-control.sh` is a preparation reminder, not a bridge launcher. Its optional
interactive Bash hook shows once per boot for eligible human terminals and skips
recognized Codex/Desktop Commander ancestry. Detection is a convenience heuristic,
not a security boundary. A verified SSH executable ends the ancestry walk;
protected root SSH parents are not required to be readable. Other ambiguous
ancestry suppresses automatic display. `remote-control.sh --show` explicitly shows it again.
Setup checks the relevant requirements before mutation; preview reports missing
requirements without writing files. `check-requirements.sh hook|service|tools|all`
is read-only and never installs or downloads dependencies. Terminal reminder
banners use color; the copy/paste instruction remains plain.
Explicit setup records hook and service versions separately in private user state.
On eligible human logins, installed/checkout mismatches warn once per boot and
version pair independently of the ordinary reminder. They suggest explicit setup;
they never install updates or restart services. Versions are compared literally.
`--show` is unconditional and does not consume either automatic notice.
Service updates reload the user manager but leave a running connection untouched;
starting or restarting the bridge is a separate human operation.

The human prepares the VM and starts and connects Remote Desktop Commander.
If the connection is unavailable, report it and wait for the human to restore it;
do not attempt setup yourself.

Only perform a preflight when explicitly requested. Keep it read-only: confirm the
remote account, absolute bundle and supplied project paths, readable instructions,
and availability/versions of Bash, Git, Python 3, Node.js, npm/npx, and Codex.
Do not invoke npx, the runner, or a Codex task; install, update, sign in, write files, change
permissions, or alter services or network policy. Report missing prerequisites
for the human to resolve, then wait for a task.

On receipt, offer an optional Codex workspace readiness check to identify missing tools or dependencies that could improve development efficiency. Only run this check with explicit human approval. Codex must report missing tooling rather than install it or silently work around it.

## Remote boundaries and security

ChatGPT's local sandbox and the connected VM are separate environments. Use Remote
Desktop Commander to inspect the VM, confirm remote paths, and run remote commands.
Do not assume local paths exist remotely.

Use the existing nonprivileged account and respect filesystem, permission, and
network restrictions. Report access failures or missing tools instead of bypassing
restrictions or changing setup. Leave firewall rules and isolation to the human.
Protect connection tokens and credentials; keep secrets out of task text and
notification labels.

## Choose direct editing or Codex delegation

Use the simplest authorized workflow for the requested task; do not launch Codex by default.

- **ChatGPT direct via Remote Desktop Commander**: architectural decisions,
  specification writing and maintenance, focused Markdown/documentation changes,
  and small cross-file consistency edits. Read the exact relevant files and applicable
  AGENTS.md instructions first; edit only when the human explicitly requests it.
  Prefer surgical edits, inspect the result, and report what changed.
- **Codex via the wrapper**: feature implementation, code changes requiring exploration,
  refactoring, tests, and larger technical work where an autonomous coding agent
  materially helps. Also use Codex if the human explicitly asks for it.
- **Judgment**: a new complex spec may merit Codex if broad repository exploration
  is needed, but document-only work does not automatically require delegation.
  When the appropriate path is unclear, briefly propose one rather than silently
  starting an agent.

For either path, work in the existing project directory. Read applicable AGENTS.md
instructions, keep to the requested scope, and preserve unrelated edits. Do not
initialize Git just to perform a task. Never edit the same files concurrently with
an active Codex task; let it finish or obtain explicit direction before proceeding.

For direct/manual edits, use the START/END notification procedure below. For Codex,
use the wrapper and its built-in notifications. Do not automatically monitor Codex
after launching it; inspect results only when the human requests a follow-up.

## Delegate implementation or substantial project work to Codex

Resolve this bundle's absolute path on the VM. Delegate project work through its
`bootstrap/tools/run-codex.sh`, passing the confirmed project path and a task file, or `--task` followed by a quoted,
self-contained task that includes relevant constraints and verification:

```bash
"$HOME/codex-starter-kit/bootstrap/tools/run-codex.sh" \
  "$HOME/codex-starter-kit" \
  --task "Read root AGENTS.md and summarize the bootstrap tools. Do not edit files."
```

Adapt these home-relative examples to the VM, explicitly confirm the resolved
absolute bundle and project paths, and replace the task with the requested work.
The wrapper sends START and END automatically; do not add manual notifications or ask Codex to send
duplicates. Keep the remote session identifier for long commands; do not automatically
poll or retrieve results. The human requests any follow-up.

## Manual tasks: send START and END

For explicitly requested manual/non-Codex tasks, use the stateful helper:

```text
manual-task.sh start PROJECT LABEL
manual-task.sh end TOKEN EXIT_CODE
manual-task.sh status
```

START prints a random 32-hex token alone on stdout and its notification on stderr.
Save the token unchanged; END retrieves the original project, label and start time.
Capture the task's exit status immediately and send END on failure as well as
success. The helper's status is not the task's status. Do not retry START after
losing its output: use `status` to find the token first.

Run this example only for a requested task, never on receipt or during preflight:

```bash
(
  set +e
  manual="$HOME/codex-starter-kit/bootstrap/tools/manual-task.sh"
  cd "$HOME/codex-starter-kit" || exit 2
  token=$("$manual" start codex-starter-kit 'Check root AGENTS.md exists') || exit 2
  bash -c 'pwd; test -f AGENTS.md'
  command_status=$?
  "$manual" end "$token" "$command_status" || true
  exit "$command_status"
)
```

Manual and Codex tasks share a per-user active count, displayed after each
transition: `🤖 1 🚀 START Codex [project] task preview`,
`🤖 0 ✅ END Codex [project] duration: 42s`, or
`🤖 0 ❌ ERROR Codex [project] duration: 18s`. Notices have timestamps; manual tasks say
`Manual`. Zero is always shown. Terminal notices are colored and END rings a bell;
stdout is plain. Labels are sanitized and truncated.

The registry is private under `/tmp/codex-starter-kit-tasks-UID` and requires
Python 3 and Linux `/proc`. It locks updates and notification delivery together.
Dead/reused Codex runner processes and manual tasks older than 24 hours are
excluded from status and pruned on the next successful START/END. Manual tasks
can span remote sessions; finish them within 24 hours. `status` is read-only and
shows active tokens and stale counts. It never monitors tasks or changes services.
A rejected END preserves other tasks; retry a failed END with the same token.
An unknown/expired token returns 3; invalid input or registry errors return 2.
An END can still finish a stale token until a successful transition prunes it.
Malformed registry entries reject transitions before writing state or sending a
notice. Do not edit the registry to change counts; counts come from task records.

The runner accepts `PROJECT_DIR TASK_FILE` (relative task paths resolve from the
caller's directory) or `PROJECT_DIR --task "instructions"`, loads NVM when present,
and passes instructions through stdin. It uses configured Codex permissions,
adding only `--skip-git-repo-check` outside Git. Its EXIT trap sends END and prints
`SANDBOX_HANDOFF_EXIT_CODE=N`; HUP/INT/TERM map to 129/130/143. Bash may defer traps
while waiting for a foreground command. SIGKILL cannot send END. Notifications
are best effort: inaccessible terminals, broken output or a crash after state
commit can lose a notice. The registry remains the count authority; notifications
do not prove success. Do not delete registry files while tasks are running.

## Verify and report

Once the human explicitly requests a follow-up, inspect the task's exit status and output, review changes, and run relevant checks before reporting success. Summarize the result, validation performed, and any remaining blockers. Report failures accurately, including incomplete work.

Do not automatically monitor or retrieve results after launching a Codex task.
