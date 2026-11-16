<!-- codex-starter-kit version: 1.0.0 -->

## Security

- **Before connecting, configure a firewall outside the VM.** Block access to the
  LAN, host, and management services while allowing necessary Internet access and
  any trusted administration access. Keep a VM console available. The human
  manages firewall policy; agents must not weaken it.
- Use a disposable, isolated VM and keep backups. Keep production credentials,
  personal files, and host mounts out of it. NAT and Codex's workspace sandbox do
  not replace VM/network isolation.
- Keep connection tokens private. Remote commands can affect anything the VM
  account can access.
- When syncing files, exclude secrets and local configuration and use only trusted
  administration access allowed by the firewall. Keep Git operations and final
  review/integration on your trusted local machine.
- Review changes before committing, publishing, or deploying.
