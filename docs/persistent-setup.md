# Persistent setup

Use this mode for an SSH login reminder and a systemd user service for Remote Desktop Commander. Installation does **not** start the bridge automatically.

After completing the [requirements](requirements.md), run these commands in your VM account:

```bash
~/codex-starter-kit/bootstrap/setup.sh --install-hook
~/codex-starter-kit/bootstrap/setup.sh --install-service
```

The hook displays the reminder in eligible interactive Bash SSH sessions. Start the service and attach to its terminal:

```bash
systemctl --user start desktop-commander.service
tmux attach -t desktop-commander
```

Use the displayed connection details to connect from ChatGPT, then paste the reminder's prompt into the conversation. To display it again:

```bash
~/codex-starter-kit/bootstrap/remote-control.sh --show
```

Restarting the bridge disconnects its current session. For notification settings and service configuration, see [advanced usage](advanced-usage.md).

## Troubleshooting: interactive prompts in tmux

The persistent bridge runs inside a tmux session. If Remote Desktop Commander stops responding, it may be waiting for an interactive confirmation (for example, an update prompt) in that session.

Connect to the VM over SSH and inspect the terminal:

```bash
tmux attach -t desktop-commander
```

If a prompt is present, read it and respond as appropriate (for example, `y` to confirm an intended update). Then detach **without terminating the session**: press `Ctrl+B`, release both keys, then press `D`.

Try connecting from ChatGPT again. If no prompt is visible, inspect the user service instead of assuming an update is blocking it:

```bash
systemctl --user status desktop-commander.service
```

Detaching tmux does not automatically answer future prompts; another interactive update may require the same intervention.
