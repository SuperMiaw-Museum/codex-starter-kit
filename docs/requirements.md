# Requirements

Complete the security requirements and prepare the VM account before choosing a startup mode.

## Security reminders

Before connecting ChatGPT or running an agent:

- **Isolate the VM at the network boundary.** Configure firewall rules **outside the VM** to block access to your LAN, host and management services, allowing only explicitly required Internet and trusted administration access. Keep VM console access available. Firewall policy is managed by the human; agents must not weaken it.
- **Use a disposable, isolated Linux VM and a dedicated nonprivileged account.** Keep backups. NAT and Codex's workspace sandbox do not replace VM and network isolation.
- **Keep sensitive data outside the VM.** Do not expose production credentials, personal files or host mounts. Keep bridge connection tokens private: remote commands can affect everything accessible to the VM account.
- **Sync cautiously.** Exclude secrets and local configuration when syncing files, and use only trusted administration access permitted by the firewall.
- **Review before integrating.** Review changes before committing, publishing or deploying. Keep Git operations and final review/integration on your trusted local machine.

## Example: VirtualBox network isolation

Network layout (illustrative; `X` marks traffic blocked by OPNsense rules):

```text
                  Physical host
┌──────────────────────────────────────────────────┐
│                                                  │
│  Host / LAN                                      │
│      │                                           │
│      │ ADMIN (Host-only)                         │
│      ▼                                           │
│  ┌────────────────────┐                          │
│  │ OPNsense VM        │──── WAN (NAT) ───────┐   │
│  │                    │                      │   │
│  │  Firewall rules    │                      │   │
│  └─────────┬──────────┘                      │   │
│            │ CODEX (Internal Network)        │   │
│            │                                 │   │
│       ┌────┴─────────┐                       │   │
│       │ Debian VM    │                       │   │
│       │ Codex        │                       │   │
│       └──────────────┘                       │   │
│                                              │   │
└──────────────────────────────────────────────┼───┘
                                               ▼
                                           Internet

        Debian VM ──X──► Host / LAN
        Debian VM ──X──► ADMIN / Management
        Debian VM ─────► Internet (filtered)
```

The blocked paths represent firewall policy, not physical disconnection.
VirtualBox NAT alone does not isolate the VM from private networks.

The following example uses VirtualBox, OPNsense and Debian to isolate the Codex environment. Other hypervisors, firewalls and Linux distributions can provide equivalent isolation.

Create two virtual machines:

- **Gateway (OPNsense):** routes and filters network traffic.
- **Sandbox (Debian):** runs Codex CLI and Remote Desktop Commander.

Configure their network adapters as follows:

| VM | Adapter | VirtualBox network | Purpose |
|---|---|---|---|
| OPNsense | WAN | NAT | Internet access |
| OPNsense | ADMIN | Host-only | Administration from the host |
| OPNsense | CODEX | Internal Network | Isolated sandbox network |
| Debian | CODEX | Internal Network | Access through OPNsense only |

Configure OPNsense with the following firewall policy:

- Allow outbound connections from CODEX by default, except to restricted destinations.
- Block access from CODEX to the host's LAN and other private networks reachable through the WAN.
- Block access from CODEX to the ADMIN network and OPNsense management interfaces, except for explicitly required services.
- Allow administration from ADMIN to the sandbox's SSH port through a restricted NAT/port-forwarding rule.
- Do not expose the sandbox's SSH service through the WAN.

**Important:** VirtualBox NAT does not guarantee isolation from the host's LAN. OPNsense must explicitly filter these destinations. Review default firewall rules, including any permissive LAN rules, and account for IPv6.

Before connecting ChatGPT, verify that the sandbox can access the Internet but cannot reach the host's LAN, the administration network or the firewall's management interfaces. Verify that SSH access works only through the intended administration path.

_Note: I replaced OPNsense with Debian for easier maintenance and faster boot times. Both Debian machines were configured with ChatGPT’s help, including automatic updates at boot with a 24-hour cooldown. The firewall VM now uses bridged networking, and I access the internal machine through it using SSH ProxyJump._

## File synchronization

The VM's network isolation should not depend on granting it access to remote Git repositories. For optional file transfers between your trusted machine and the VM without using Git inside the VM, see [codex-sync](https://github.com/SuperMiaw/codex-sync). This is a convenience tool, not a security boundary or a mandatory dependency.

## Codex CLI

Install and authenticate Codex CLI under the same VM account that will run the bridge, and verify that `codex` is available.

To use codex-starter-kit to its full potential, Codex may need the following settings in `~/.codex/config.toml`:

```toml
sandbox_mode = "danger-full-access"
approval_policy = "never"
```

These settings let Codex execute tasks autonomously without approval prompts or workspace restrictions. **This is why network isolation matters:** treat the VM as an untrusted environment and restrict access to your LAN, host, and sensitive resources using a firewall outside the VM. Codex CLI may display its own security warning when these settings are enabled.

If you are unsure how to configure or verify the isolation, ask ChatGPT to help you set up the firewall and test the network boundaries **before** enabling these settings.

## Remote Desktop Commander

1. Find and connect **Remote Desktop Commander** in ChatGPT under **Settings → Plugins**.
2. Follow the plugin's setup instructions to install or configure any required component on the computer hosting the VM, if applicable. See the [Remote Desktop Commander project](https://github.com/wonderwhy-er/DesktopCommanderMCP).
3. Verify that Remote Desktop Commander can reach the sandbox VM before starting the workflow.

## Setup verification

Clone the kit into the VM account:

```bash
git clone https://github.com/SuperMiaw/codex-starter-kit ~/codex-starter-kit
```

Check the required tools before starting. The script reports missing dependencies without installing or modifying anything:

```bash
~/codex-starter-kit/bootstrap/check-requirements.sh all
```

The `all` profile includes persistent-service requirements; one-shot mode does not need systemd or tmux.

## Next step

Choose [one-shot startup](one-shot-startup.md) for a foreground session without installing a hook or service, or [persistent setup](persistent-setup.md) for the SSH reminder and systemd user service.
