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
