# Advanced usage

These features are optional. Complete the [requirements](requirements.md) and choose a startup mode before configuring them.

## Cloned repositories

To make cloned Git repositories available to the agent, create symlinks under these optional directories in the VM account's home:

- `~/.codex-starter-kit-repos-ro/`: repositories for inspection only.
- `~/.codex-starter-kit-repos-rw/`: repositories for inspection and scoped editing.

A repository linked in both is treated as read-only; the agent reports the conflict. The kit does not create these directories or links. This convention guides agent behavior but **does not enforce filesystem permissions** or replace network isolation. Git and destructive-operation safeguards still apply. See the [repository access instructions](../bootstrap/skills/REPOSITORIES.md) for details.

**Recommendation:** Prefer public repositories cloned without credentials, so the agent can inspect and edit files locally without being able to push changes. If remote write access is necessary, use narrowly scoped credentials restricted to the intended repositories. Keep Git push and publication operations on your trusted machine whenever possible.

## Dedicated playground VM

Ask ChatGPT to activate the [Playground skill](../bootstrap/skills/PLAYGROUND.md)
by name for tasks that need sudo or Docker. Prepare a separate, isolated,
SSH-accessible VM without user data or secrets, and explicitly authorize its use.

## Notification options

The Codex runner broadcasts START, END and ERROR notifications to SSH terminals on a best-effort basis. By default, START shows a one-line preview of the task (up to 120 characters, with `...` when truncated); END and ERROR include the duration and available model, token count and session ID. Verify task results independently of notifications.

| Variable | Default | Effect |
| --- | --- | --- |
| `CODEX_STARTER_KIT_SHOW_FULL_PROMPT` | `0` | Set to `1` to include the complete delegated prompt in START notifications. Be mindful of sensitive task text. |
| `CODEX_STARTER_KIT_LIMIT_REMINDER` | `0` | Set to `1` to show the snapshot age and remaining 5-hour and weekly quotas after Codex END or ERROR. Stale or expired snapshots show only their age and status. If no usable snapshot exists, the notice says `Codex usage: unavailable`. |
| `CODEX_STARTER_KIT_LIVE_INSPECT` | `0` | Set to `1` to run each Codex handoff in a dedicated tmux session for read-only live inspection. |

### Persistent service (systemd)

Edit the service override:

```bash
systemctl --user edit desktop-commander.service
```

For example, to enable full prompts and live inspection:

```ini
[Service]
Environment=CODEX_STARTER_KIT_SHOW_FULL_PROMPT=1
Environment=CODEX_STARTER_KIT_LIVE_INSPECT=1
```

Apply the changes:

```bash
systemctl --user daemon-reload
systemctl --user restart desktop-commander.service
```

Restarting the bridge disconnects its current ChatGPT session. Wait until no Codex mission is active before restarting. New handoffs inherit the environment from the restarted bridge.

With live inspection enabled, the runner prints a `tmux -S ... attach -r -t ...` command. Run it from another terminal on the same host to observe the active mission without interacting with Codex. Detach with `Ctrl+B`, then `D`. The inspection session disappears when the mission finishes; it is not a persistent log. The tmux session identifier is distinct from the Codex session ID used by `--resume`.

### One-shot session

Export the variables in the terminal before starting the bridge, for example:

```bash
CODEX_STARTER_KIT_SHOW_FULL_PROMPT=1 ./bootstrap/remote-control.sh --once
```

## Resume a Codex session

Ask ChatGPT to continue a previous Codex task and provide the session ID from
the END notification.

The session must already exist locally. An invalid or unavailable session fails rather than silently starting a new one. Omit `--resume` to start a fresh session.

## Project resources

Adapt an [AGENTS.md template](../templates/agents/) for project-specific instructions.
Use the [reusable prompts](../templates/prompts/) in ChatGPT for project status,
commit message suggestions, or remaining work.
