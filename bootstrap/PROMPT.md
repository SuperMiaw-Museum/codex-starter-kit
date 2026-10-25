# Remote work instructions for ChatGPT

## On receipt: acknowledge and wait

Treat this prompt as instructions, not a task. Acknowledge it and wait for the
human's request. Do not run commands, launch Codex, execute examples, edit files,
install tools, authenticate, start services, or change the environment on receipt.

The human prepares the VM and starts and connects Remote Desktop Commander.
If the connection is unavailable, report it and wait for the human to restore it;
do not attempt setup yourself.

Only perform a preflight when explicitly requested. Keep it read-only: confirm the
remote account, absolute bundle and supplied project paths, readable instructions,
and availability/versions of Bash, Git, Python 3, Node.js, npm/npx, and Codex.
Do not invoke npx, the runner, or a Codex task; install, update, sign in, write files, change
permissions, or alter services or network policy. Report missing prerequisites
for the human to resolve, then wait for a task.

## Remote boundaries and security

ChatGPT's local sandbox and the connected VM are separate environments. Use Remote
Desktop Commander to inspect the VM, confirm remote paths, and run remote commands.
Do not assume local paths exist remotely.

Use the existing nonprivileged account and respect filesystem, permission, and
network restrictions. Report access failures or missing tools instead of bypassing
restrictions or changing setup. Leave firewall rules and isolation to the human.
Protect connection tokens and credentials; keep secrets out of task text and
notification labels.

## Delegate project work to Codex

Work in the existing project directory. Read its applicable AGENTS.md instructions,
keep to the requested scope, and preserve unrelated edits. Do not initialize Git
just to run Codex.

Resolve this bundle's absolute path on the VM. Delegate project work through its
`bootstrap/tools/run-codex.sh`, passing the confirmed project path and a quoted,
self-contained task that includes relevant constraints and verification:

```bash
"$HOME/codex-starter-kit/bootstrap/tools/run-codex.sh" \
  "$HOME/codex-starter-kit" \
  "Read root AGENTS.md and summarize the bootstrap tools. Do not edit files."
```

Adapt these home-relative examples to the VM, explicitly confirm the resolved
absolute bundle and project paths, and replace the task with the requested work.
The wrapper sends START and END automatically; do not add manual notifications or ask Codex to send
duplicates. Keep the remote session identifier for long commands and read output
until completion.

## Manual tasks: send START and END

For explicitly requested manual/non-Codex tasks, use the notification helper:

```text
notify-task-done.sh start PROJECT TASK
notify-task-done.sh end PROJECT EXIT_CODE TASK START_EPOCH_TOKEN
```

Save the token printed by `start` and pass it unchanged to `end`, using the same
project and task strings, even across remote sessions. Capture the command's exit
status immediately and send END on ordinary failure as well as success. The
helper's status is not the task's status.

This example checks for a file in the existing checkout. Run it only for a
requested task, never on receipt of this prompt or during preflight. Adapt the
paths, label, and command together:

```bash
(
  set +e
  notify="$HOME/codex-starter-kit/bootstrap/tools/notify-task-done.sh"
  project=codex-starter-kit
  task='Check root AGENTS.md exists'
  cd "$HOME/codex-starter-kit" || exit 2

  start_token=$("$notify" start "$project" "$task") || exit 2
  bash -c 'pwd; test -f AGENTS.md'
  command_status=$?
  "$notify" end "$project" "$command_status" "$task" "$start_token" || true
  exit "$command_status"
)
```

For a failure example, replace the command with `bash -c 'exit 7'`: END reports
ERROR (7), and the block returns 7. Keep the session alive until completion;
abrupt termination can prevent END. Notifications are best effort and do not prove
that a task succeeded.

## Verify and report

Inspect the process exit status and output, review changes, and run relevant
checks before reporting success. Summarize the result, validation performed, and
any remaining blockers. Report failures accurately, including incomplete work.
