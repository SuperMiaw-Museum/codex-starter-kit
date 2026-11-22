# Architecture

The human operates ChatGPT and a terminal connected to an isolated Linux VM.
Remote Desktop Commander gives ChatGPT access to that VM. ChatGPT may make
small authorized edits directly or delegate implementation to Codex CLI through
the wrapper. The human reviews task results and integrates changes.

The VM and ChatGPT local sandbox are separate filesystems. The bridge connection
does not replace external firewall isolation. The terminal notifications are
best-effort signals, not proof that a task succeeded.

Human entrypoints live at the bootstrap root. The root prompt directs ChatGPT
to orchestration commands and extensions in `bootstrap/orchestration/`.
Shell entrypoints delegate to Python and shared shell code in
`bootstrap/internal/`. The service unit invokes `bootstrap/internal/desktop-commander.sh`
and the SSH hook invokes `bootstrap/remote-control.sh`.

![Architecture](../assets/architecture.svg)

See [Security](security.md), [Bootstrap](bootstrap.md), and
[Notifications](notifications.md) for operational contracts.
