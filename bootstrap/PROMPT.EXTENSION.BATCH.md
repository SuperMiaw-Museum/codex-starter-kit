# Batch orchestration — conditional instructions for ChatGPT

Read this extension only when evaluating a potentially multi-task Codex workflow. It supplements `PROMPT.md`; all its authorization and safety boundaries remain in force.

Use a batch only when multiple tasks can be specified in advance, must run **sequentially** on the same project, and do not require human decisions between tasks. This is not parallel execution. Propose the batch and obtain explicit human approval before launching it. Use the normal direct runner for ordinary single-task requests. The batch uses existing Codex authentication.

## Prepare a manifest

Create a JSON file, for example `batch.json`:

```json
{
  "version": 1,
  "project": "/absolute/path/to/my-project",
  "tasks": [
    {
      "id": "implement",
      "prompt": "Implement the requested change. Do not commit."
    },
    {
      "id": "verify",
      "prompt": "Review the change, run relevant tests, and fix regressions. Do not commit."
    }
  ]
}
```

Each task needs a unique `id` and **exactly one** of `prompt` (inline text) or `prompt_file` (path to a UTF-8 text file). The task list must not be empty. Relative project and prompt-file paths are resolved relative to the manifest's directory. The project directory must already exist.

For longer instructions, use `"prompt_file": "prompts/implement.txt"` instead of `prompt`. The batch snapshots the prompt contents when it starts; later edits to those files do not change an existing batch.

Keep each task independently understandable. A later task may rely on files created by an earlier one, but it is not automatically a continuation of the same Codex conversation.

## Run and inspect

After explicit approval, from the starter-kit checkout:

```bash
bootstrap/tools/batch.sh run /path/to/batch.json
```

The command prints `BATCH_ID=<id>` once the batch has been created. Save this ID to inspect or resume the batch:

```bash
bootstrap/tools/batch.sh status <id>
bootstrap/tools/batch.sh resume <id>
```

`status` prints JSON with the overall status and each task's status. It does not start Codex. `resume` checks durable results and continues only when it can establish that preceding tasks succeeded.

Batch state is stored under `~/.codex-starter-kit-batches/<id>/`, separately from the project. Do not edit the state files or receipts by hand.

## Failures and recovery

| Result | Meaning | What to do |
| --- | --- | --- |
| `succeeded` | Task completed successfully, with a verified receipt. | No action needed. |
| `pending` | Task has not been started. | Use `resume` if preceding tasks succeeded. |
| `failed` | A task ended with a confirmed failure or cancellation. | Inspect the project and output; create a **new manifest** for the remaining work. |
| `unknown` | An attempt was started, but its outcome cannot be established safely. | Inspect the project, running processes, and receipts before planning a new batch. Never assume the task did nothing. |
| `BUSY` (exit 75) | A cooperating runner already reserves this project. | Wait for it to finish; do not run competing tasks. |
| `UNKNOWN` (exit 76) | The project is fenced or an attempt is inconclusive. | Investigate the running descendants and project state; do not bypass the fence or delete coordination files. |

A batch **stops on its first unsuccessful task**. It never silently retries a failed or ambiguous attempt. Re-running `resume` is not a way to force a retry. A terminated supervisor may leave an `unknown` attempt, and a forced kill can prevent the usual END notification.

If a task failed or is unknown, first review its partial edits. When it is safe to proceed, prepare a **new batch** with only the work that still needs doing; avoid blindly repeating non-idempotent changes.

## Scope and limitations

The reservation lasts across task boundaries and prevents **cooperating starter-kit runners** from interleaving work on the same canonical project directory. The project lock is released after normal completion; an uncertain descendant may leave a protective fence.

This is **not a security sandbox**: another terminal, an external Codex launcher, or a process with the same user permissions can still modify the files. Do not run unrelated tasks against the project while its batch is active.

The MVP supports one canonical project root per batch in direct mode. It does not coordinate nested repositories, worktrees, tmux batches, or parallel read-only tasks. Bubblewrap and the model relay are experimental work outside this workflow.

For implementation details and technical guarantees, consult the relevant contract in `specs/` only when needed.
