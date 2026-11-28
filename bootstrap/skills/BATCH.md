# Batch orchestration — conditional instructions for ChatGPT

Read this skill only when evaluating a potentially multi-task Codex workflow. It supplements `bootstrap/SYSTEM_PROMPT.md`; all its authorization and safety boundaries remain in force.

Use a batch when multiple tasks can be specified in advance and require no human decisions between tasks. Propose the batch and obtain explicit human approval before launching it. Use the normal direct runner for ordinary single-task requests. The batch uses existing Codex authentication.

## Prepare a manifest

Each task needs a unique `id` and **exactly one** of `prompt` (inline text) or `prompt_file` (path to a UTF-8 text file). The task list must not be empty. Relative project and prompt-file paths are resolved relative to the manifest's directory. The project directory must already exist.

For longer instructions, use `"prompt_file": "prompts/implement.txt"` instead of `prompt`. The batch snapshots the prompt contents when it starts; later edits to those files do not change an existing batch.

Keep each task independently understandable. A later task may rely on files created by an earlier one, but it is not automatically a continuation of the same Codex conversation.

Set `version` to 2 and put `project` on every task. Use `depends_on` for task IDs whose success is required before a task can start. Independent tasks can overlap when their declared resources are compatible and both parallel limits permit it.

```json
{
  "version": 2,
  "max_parallel": 2,
  "tasks": [
    {"id": "generate", "project": "./api", "prompt_file": "prompts/generate.txt"},
    {"id": "lint", "project": "./frontend", "prompt": "Run the frontend lint checks."},
    {"id": "verify", "project": "./api", "prompt": "Verify the generated output.", "depends_on": ["generate"]}
  ]
}
```

The manifest limit and the host setting `CODEX_STARTER_KIT_BATCH_HOST_MAX_PARALLEL` each default to 1. The host setting coordinates batch supervisors under the same account and lock directory. Active batches must use the same host setting. Direct runs and batches share resource locks and fences.

## Run and inspect

After explicit approval, from the starter-kit checkout:

```bash
bootstrap/orchestration/batch.sh run /path/to/batch.json
```

The command prints `BATCH_ID=<id>` once the batch has been created. Save this ID to inspect or resume the batch:

```bash
bootstrap/orchestration/batch.sh status <id>
bootstrap/orchestration/batch.sh resume <id>
```

`status` prints JSON with the overall status and each task's status. It does not start Codex. `resume` checks durable results and continues from confirmed successes. A failed or ambiguous task anywhere in the batch blocks resume.

Batch state is stored under `~/.codex-starter-kit-batches/<id>/`, separately from the project. Do not edit the state files or receipts by hand. Version 1 manifests and states are rejected with exit code 2. Existing files are preserved without migration.

## Failures and recovery

| Result | Meaning | What to do |
| --- | --- | --- |
| `succeeded` | Task completed successfully, with a verified receipt. | No action needed. |
| `pending` | Task has not been started. | Use `resume` if its dependencies succeeded and the batch has no failed or ambiguous attempts. |
| `failed` | A task ended with a confirmed failure or cancellation. | Inspect the project and output; create a **new manifest** for the remaining work. |
| `unknown` | An attempt was started, but its outcome cannot be established safely. | Inspect the project, running processes, and receipts before planning a new batch. Never assume the task did nothing. |
| `BUSY` (exit 75) | A cooperating runner already reserves this project. | Wait for it to finish; do not run competing tasks. |
| `UNKNOWN` (exit 76) | The project is fenced or an attempt is inconclusive. | Investigate the running descendants and project state; do not bypass the fence or delete coordination files. |

A batch **stops admitting tasks at the first observed failure**. Active tasks drain and can still publish results. HUP, INT and TERM are forwarded to active supervisors. Failed or ambiguous attempts are never retried automatically. Re-running `resume` is not a way to force a retry. A terminated supervisor may leave an `unknown` attempt, and a forced kill can prevent the usual END notification.

If a task failed or is unknown, first review its partial edits. When it is safe to proceed, prepare a **new batch** with only the work that still needs doing; avoid blindly repeating non-idempotent changes.

## Scope and limitations

The batch holds its resource reservation across task boundaries. Each task reserves its main project for writing and includes all configured REPOSITORIES links. Read-only external declarations take precedence over duplicate writable external declarations. Declaring the main project read-only is an admission error. A writable external dependency can prevent otherwise independent tasks from overlapping. A resource change detected before execution stops the affected task before Codex starts. An unconfirmed descendant can leave a protective fence.

This is **not a security sandbox**: another terminal, an external Codex launcher, or a process with the same user permissions can still modify the files. Do not run unrelated tasks against the project while its batch is active.

Batches use direct mode. Ambiguous nested roots, linked Git worktrees and unsupported mount layouts are refused. RO expresses cooperation among launchers and does not enforce filesystem permissions.

For implementation details and technical guarantees, see the [batch execution contract](../../specs/batch-execution.md).
