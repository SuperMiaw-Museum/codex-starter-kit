# Persistent setup

Use this mode for an SSH login reminder and a systemd user service for Remote Desktop Commander. Installation does **not** start the bridge automatically.

After completing the [requirements](requirements.md), run these commands in your VM account:

```bash
~/codex-starter-kit/bootstrap/setup.sh --install-hook
~/codex-starter-kit/bootstrap/setup.sh --install-service
```

The hook displays the reminder in every eligible interactive Bash SSH session. Start the service and attach to its terminal:

```bash
systemctl --user start desktop-commander.service
tmux attach -t desktop-commander
```

Use the displayed connection details to connect from ChatGPT, then paste the reminder's prompt into the conversation. To display it again:

```bash
~/codex-starter-kit/bootstrap/remote-control.sh --show
```

After updating the checkout, rerun `setup.sh` for any installed components you want to refresh. Changes are applied only when needed; the bridge is never restarted automatically.

For notification settings and service configuration, see [advanced usage](advanced-usage.md).

## Troubleshooting

If Remote Desktop Commander stops responding, check whether it is waiting for input:

```bash
tmux attach -t desktop-commander
```

Respond to any pending prompt, then detach with `Ctrl+B`, `D` without stopping the session.

If no prompt is waiting, check the service:

```bash
systemctl --user status desktop-commander.service
```
