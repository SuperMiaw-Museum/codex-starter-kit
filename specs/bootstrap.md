## Getting started

1. **Prepare the VM.** Follow the [security requirements](../docs/requirements.md#security-reminders) before connecting. Use a
   nonprivileged account without administrative access. Install Bash, Git,
   Python 3.8 or newer, Node.js with npm/npx, Codex CLI, and your project's tools. Complete
   Codex sign-in as that account.

2. **Clone this kit on the VM.** In that account's terminal, replace the placeholder
   with this repository's clone URL:

   ```bash
   git clone https://github.com/SuperMiaw/codex-starter-kit ~/codex-starter-kit
   ```

3. **Prepare the reminder and bridge.** The reminder does not launch or download
   Desktop Commander. Preview the optional service and explicitly install either
   component when ready:

   ```bash
   ~/codex-starter-kit/bootstrap/setup.sh --preview
   ~/codex-starter-kit/bootstrap/setup.sh --install-hook
   ~/codex-starter-kit/bootstrap/setup.sh --install-service
   ```

   Run `bootstrap/check-requirements.sh hook`, `service`, `tools`, or `all`
   (the default) for a read-only prerequisite report. OK/KO rows for only the selected profile and a summary
   use color on a terminal and plain text when redirected. Exit status is 0 when
   the selected profile passes, 1 for missing requirements, and 2 for invalid usage.
   Every profile checks Bash, `dirname`, and Python 3.8 or newer. The `hook`
   profile also checks Linux procfs and `/usr/bin/bash`. The `service` profile
   checks tmux, node/npx, a reachable `systemctl --user`, and the unit's fixed
   paths for Bash and tmux. The `tools` profile checks procfs, Git, and Codex CLI.
   Service/tools resolve the existing NVM environment as their wrappers do.
   Nothing is installed or downloaded. Setup runs its relevant profile before any
   mutation. With a supported Python interpreter, preview checks the service
   profile, renders the unit even when that profile fails, returns the check
   status, and writes no files. If Python is missing, the preflight prints a
   minimal report and setup exits with a diagnostic.

   Hook setup preserves existing `.bashrc` content and writes the managed
   block only when the resulting file differs. The hook runs on interactive Bash
   startup (including login shells whose profile sources `.bashrc`). If your login profile does not source
   `.bashrc`, arrange that yourself. No setup runs automatically from this kit.
   Every eligible interactive human SSH terminal displays the reminder. Use
   `bootstrap/remote-control.sh --show` to display it at any time, without consuming
   the automatic reminder. The displayed prompt uses this checkout's absolute path.
   Both manual and automatic banners use restrained ANSI color on a terminal;
   redirected output stays plain and the copy/paste instruction is unchanged.

   Automatic display requires Python, a terminal, and readable Linux process
   ancestry. The hook stays silent if Python is missing. The ancestry check
   skips Codex and Desktop Commander by executable name and known interpreter script positions.
   SSH titles such as `sshd-session: codex@pts/1` and arbitrary argument text do
   not identify agents. SSH boundaries are verified through matching procfs `comm` and status `Name`,
   `PPid`, and `Uid`: session processes lead through a root session to a root
   `sshd` listener parented by PID 1. SSH processes need not expose `exe` or
   `cmdline`; every non-SSH descendant must expose both for agent inspection. Other unreadable
   ancestry fails closed. Renamed processes,
   unusual launchers, and detached ancestry can evade detection. This is a convenience
   heuristic, not a security boundary.

   Service setup compares the full unit content,
   backs up changed units to `.service.backup`, and runs user `daemon-reload`
   after a change or when a pending reload marker exists from an earlier
   installation attempt. Rerunning `--install-service` retries the reload
   without rewriting an unchanged unit or backup. The marker is removed after
   a successful reload. Setup never starts, stops, restarts, or enables the bridge. It
   preserves the installed unit's tmux session, NVM loading, and restart policy.
   Keep this checkout at its configured path; rerun setup after moving it. When
   ready, manually start the service with
   `systemctl --user start desktop-commander.service`, or restart an existing one
   with `systemctl --user restart desktop-commander.service`. Restart disconnects
   the current connection. Optional activation on user manager startup is
   `systemctl --user enable desktop-commander.service`; VM boot availability
   depends on your user-manager/login configuration. The bridge may download
   its package and prints connection information in tmux (`tmux attach -t
   desktop-commander`).

   For a one-shot launch, run `bootstrap/remote-control.sh --once` instead of
   installing the hook or service. It checks for an existing service or named
   tmux session, holds a nonblocking lock on the checkout's bootstrap directory,
   prints the same reminder as `--show`, and replaces the launcher with the
   foreground `npx` process. The lock applies to one checkout. Coordinate
   starts across checkouts and with the service. See [one-shot startup](../docs/one-shot-startup.md).

4. **Connect ChatGPT.** In the ChatGPT web app, configure the **Remote Desktop
   Commander plugin** using the bridge's connection information and the plugin's
   instructions. Your account must have access to the plugin; this kit does not
   install or configure it.

5. **Paste the banner's instruction into the conversation.** It tells ChatGPT to
   read [bootstrap/SYSTEM_PROMPT.md](../bootstrap/SYSTEM_PROMPT.md) at its actual
   absolute VM path.
   Nothing is sent automatically. ChatGPT acknowledges the instructions and waits
   without starting tasks or changing the environment. Explicitly request a
   read-only connectivity/prerequisite check if wanted.

   The optional [Playground skill](../bootstrap/skills/PLAYGROUND.md) requires
   explicit activation by name and separate authorization for access and work.
   Worker setup does not configure or grant access to the playground.

6. **Give ChatGPT a task.** Supply your existing project's absolute VM path, the
   desired change, and acceptance checks. Optionally adapt a [project template](../templates/agents/)
   as the project's root `AGENTS.md`, reconciling existing instructions before
   replacing them. ChatGPT reads those instructions, delegates work to Codex, and
   reviews the result. Keep the existing project directory; do not initialize Git
   for this setup. Check task output and exit status; START/END notifications are
   best effort.

For runner commands and notification examples, see the
[handoff reference](handoff.md). Confirm absolute bundle and project paths on
the VM before running tasks. ChatGPT's local sandbox paths are separate.
