# Advanced usage

These features are optional. Complete the [requirements](requirements.md) and choose a startup mode before configuring them.

## Cloned repositories

To make cloned Git repositories available to the agent, create symlinks under these optional directories in the VM account's home:

- `~/.codex-starter-kit-repos-ro/`: repositories for inspection only.
- `~/.codex-starter-kit-repos-rw/`: repositories for inspection and scoped editing.

A repository linked in both is treated as read-only; the agent reports the conflict. The kit does not create these directories or links. This convention guides agent behavior but **does not enforce filesystem permissions** or replace network isolation. Git and destructive-operation safeguards still apply. See the [operational prompt](../bootstrap/PROMPT.md) for details.

**Recommendation:** Prefer public repositories cloned without credentials, so the agent can inspect and edit files locally without being able to push changes. If remote write access is necessary, use narrowly scoped credentials restricted to the intended repositories. Keep Git push and publication operations on your trusted machine whenever possible.

## Notification options

The Codex runner broadcasts START, END and ERROR notifications to SSH terminals on a best-effort basis. By default, START shows a one-line preview of the task (up to 120 characters, with `...` when truncated); END and ERROR include the duration and available model, token count and session ID. Verify task results independently of notifications.

| Variable | Default | Effect |
| --- | --- | --- |
| `CODEX_STARTER_KIT_SHOW_FULL_PROMPT` | `0` | Set to `1` to include the complete delegated prompt in START notifications. Be mindful of sensitive task text. |

### Persistent service (systemd)

Edit the service override:

```bash
systemctl --user edit desktop-commander.service
```

For example, to enable full prompts:

```ini
[Service]
Environment=CODEX_STARTER_KIT_SHOW_FULL_PROMPT=1
```

Apply the changes:

```bash
systemctl --user daemon-reload
systemctl --user restart desktop-commander.service
```

Restarting the bridge disconnects its current ChatGPT session.

### One-shot session

Export the variables in the terminal before starting the bridge, for example:

```bash
CODEX_STARTER_KIT_SHOW_FULL_PROMPT=1 ./bootstrap/remote-control.sh --once
```

## Project resources

Use the [project templates](../templates/) to add project-specific instructions.
