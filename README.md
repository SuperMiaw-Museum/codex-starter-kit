# codex-starter-kit
<!-- codex-starter-kit version: 2.1.0 -->

A starting point for working with ChatGPT and Codex CLI on projects inside an
isolated Linux VM. ChatGPT orchestrates work via Remote Desktop Commander;
Codex handles substantial coding tasks, and you review the results.

## Example

![Chat workflow](assets/demo-chat.png)

![Terminal notifications](assets/demo-workflow.gif)

## Architecture

![Architecture](assets/architecture.svg)

## Recommendations

Use a disposable VM, a nonprivileged account and firewall rules outside the
VM that isolate your LAN and host while allowing the access you explicitly need.
Keep credentials and personal files out of the sandbox. Review changes before
committing, publishing or deploying. See [security](specs/security.md).

## Bootstrap the sandbox

Install Bash, Git, Python 3, Node.js/npm/npx, tmux, Codex CLI and your project's
dependencies in the VM. Sign in to Codex with the VM account, then:

```bash
git clone https://github.com/SuperMiaw/codex-starter-kit ~/codex-starter-kit
~/codex-starter-kit/bootstrap/check-requirements.sh all
~/codex-starter-kit/bootstrap/setup.sh --preview
~/codex-starter-kit/bootstrap/setup.sh --install-hook
~/codex-starter-kit/bootstrap/setup.sh --install-service
```

The hook prints a reminder in eligible human Bash SSH sessions; the service
installation does **not** start the bridge. Start it yourself when ready:

```bash
systemctl --user start desktop-commander.service
```

To view connection details, use `tmux attach -t desktop-commander`. Restarting
an existing bridge disconnects its current session. Full setup behavior:
[bootstrap specification](specs/bootstrap.md).

## Connect ChatGPT and receive SSH notifications

Connect the Remote Desktop Commander plugin in ChatGPT using the bridge's
connection information. In the VM, display the prompt to copy:

```bash
~/codex-starter-kit/bootstrap/remote-control.sh --show
```

Paste its instruction into ChatGPT. It reads `bootstrap/PROMPT.md`,
acknowledges, then waits. Ask it to work on a project using its **absolute VM
path**, desired changes and acceptance checks. It can edit small tasks directly
or hand off substantial work to Codex. The runner automatically sends START,
END and ERROR notifications to your SSH terminals with a shared active-task
count. Notifications are best effort; verify actual task results.

For direct use of the runner and task status, see
[notifications](specs/notifications.md). Optional project-specific instructions
are available in [templates](templates/). You can also use
[codex-sync](https://github.com/SuperMiaw/codex-sync) to sync projects.

## External repositories

The optional reserved `~/.codex-starter-kit-repos-ro/` and
`~/.codex-starter-kit-repos-rw/` directories under the agent account's home contain
symlinks to task-relevant Git repositories: inspection only for `ro`, inspection
and scoped editing for `rw`. A repository linked in both is read-only; report the
conflict. Unrelated repositories remain outside this convention. Normal Git,
destructive and remote operation rules still apply. These are best-effort agent
instructions, not filesystem enforcement or a security boundary. Setup creates
neither directory nor links. See the [operational prompt](bootstrap/PROMPT.md)
and copy or adapt the convention with your project template.

## Technical specifications

[Architecture](specs/architecture.md) ·
[Bootstrap](specs/bootstrap.md) ·
[Handoff](specs/handoff.md) ·
[Notifications](specs/notifications.md) ·
[Security](specs/security.md) ·
[Releases](specs/releases.md)

Release tags use plain `a.b.c` versions (no `v`). Run
`./release.sh --check` before releasing, then `./release.sh` to choose a
major/minor/patch bump, or `./release.sh --bump patch`. After confirmation, it
commits version files and atomically pushes main and the annotated tag to origin.

Tag CI prepares a GitHub Release draft; it never publishes automatically.
See [releases](specs/releases.md) for explicit manual publication.
