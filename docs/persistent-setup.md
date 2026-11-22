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

After updating the checkout, rerun `setup.sh` for any installed components you
want to refresh. Changes are applied only when needed. Setup leaves a running
bridge alone.

If your installed unit points to an older `desktop-commander.sh` location, wait
for active Codex tasks to finish, then run `bootstrap/setup.sh --install-service`
before the next service start or restart. This refreshes the unit to use
`bootstrap/internal/desktop-commander.sh`. The SSH hook still invokes
`bootstrap/remote-control.sh`.

If service installation reports that `daemon-reload` failed, fix the user
manager issue and rerun `setup.sh --install-service`. The installer records
the pending reload and retries it even when the unit file already matches.
You can inspect the unit with `setup.sh --preview` without writing files or
reloading the user manager.

For notification settings and service configuration, see [advanced usage](advanced-usage.md).

## Uninstall

Run these steps as the VM user who installed the components. Skip anything you did not install.

1. Edit `~/.bashrc` and remove only the block between `# BEGIN codex-starter-kit reminder` and `# END codex-starter-kit reminder` (including the markers). Preserve all other lines. If the markers are missing or malformed, inspect the file before editing.
2. If you installed the user service, verify that `desktop-commander.service` is the kit-managed unit (its file begins with `# codex-starter-kit managed unit v1`):

   ```bash
   unit_path="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/desktop-commander.service"
   systemctl --user show -p FragmentPath desktop-commander.service
   head -n 1 -- "$unit_path"
   ```

   If the path and header match, remove the service:

   ```bash
   systemctl --user disable --now desktop-commander.service
   rm -- "$unit_path"
   systemctl --user daemon-reload
   ```

Stopping the service disconnects Remote Desktop Commander. Do not delete the repository, credentials, service backups or unrelated user files. Preserve any custom service changes before removal.

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
