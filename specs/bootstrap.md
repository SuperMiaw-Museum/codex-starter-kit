<!-- codex-starter-kit version: 1.0.0 -->

## Getting started

1. **Prepare the VM.** Follow [Security](#security) before connecting. Use a
   nonprivileged account without administrative access. Install Bash, Git,
   Python 3, Node.js with npm/npx, Codex CLI, and your project's tools. Complete
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
   Hook checks Bash, readlink, flock, Linux procfs, and setup utilities; service
   checks setup utilities, tmux (including the unit's fixed paths), node/npx, and
   a reachable `systemctl --user`; tools checks Python 3, procfs, git, and Codex CLI.
   Service/tools resolve the existing NVM environment as their wrappers do.
   Nothing is installed or downloaded. Setup runs its relevant profile before any
   mutation. Preview checks the service profile, renders the unit even on failure,
   returns the check status, and writes no files or receipts.

   Hook setup preserves existing `.bashrc` content and replaces its managed block
   on repeat runs. It runs on interactive Bash startup (including login shells
   whose profile sources `.bashrc`). If your login profile does not source
   `.bashrc`, arrange that yourself. No setup runs automatically from this kit.
   The first eligible human terminal per Linux VM boot displays the reminder;
   subsequent interactive terminals are silent. Use
   `bootstrap/remote-control.sh --show` to display it at any time, without consuming
   the automatic reminder. The displayed prompt uses this checkout's absolute path.
   Both manual and automatic banners use restrained ANSI color on a terminal;
   redirected output stays plain and the copy/paste instruction is unchanged.

   Automatic display requires a terminal, readable Linux process ancestry and
   boot ID, and `flock`. Ancestry matching skips Codex and Desktop Commander
   executable names and known interpreter script positions using Bash and readlink.
   SSH titles such as `sshd-session: codex@pts/1` and arbitrary argument text do
   not identify agents. SSH boundaries are verified through matching procfs `comm` and status `Name`,
   `PPid`, and `Uid`: session processes lead through a root session to a root
   `sshd` listener parented by PID 1. SSH processes need not expose `exe` or
   `cmdline`; every non-SSH descendant must expose both for agent inspection. Other unreadable
   ancestry fails closed. Renamed processes,
   unusual launchers, and detached ancestry can evade detection. This is a convenience
   heuristic, not a security boundary. Missing prerequisites or unsafe `/tmp`
   collisions silently skip display. State is private to the user in
   `/tmp/codex-starter-kit-reminder-UID`, serialized with `flock`, and recorded
   only after successful reminder output. Same-user processes remain trusted.

   Explicit successful hook/service setup records that component's checkout version
   in a private `0700` directory at `${XDG_STATE_HOME:-$HOME/.local/state}/codex-starter-kit`.
   Preview records nothing. Each component has its own receipt; rerunning one does
   not acknowledge the other. Old installations without receipts need explicit setup
   once before update detection is available. Versions are compared literally,
   including downgrades. On the first eligible human terminal, each mismatch warns
   once per boot and installed/checkout version pair, suggesting the matching setup
   option. These warnings have independent markers: an earlier ordinary reminder
   never hides a newly discovered mismatch. No update or restart happens automatically.
   `--show` remains unconditional and does not inspect or change state.

   Service setup compares full content (the managed unit also has a version hint),
   backs up changed units to `.service.backup`, and runs user `daemon-reload` only
   after a change. It never starts, stops, restarts, or enables the bridge. It
   preserves the installed unit's tmux session, NVM loading, and restart policy.
   Keep this checkout at its configured path; rerun setup after moving it. When
   ready, manually start the service with
   `systemctl --user start desktop-commander.service`, or restart an existing one
   with `systemctl --user restart desktop-commander.service`. Restart disconnects
   the current connection. Optional activation on user manager startup is
   `systemctl --user enable desktop-commander.service`; VM boot availability
   depends on your user-manager/login configuration. Alternatively run
   `bootstrap/tools/desktop-commander.sh` in a terminal. The bridge may download
   its package and prints connection information in tmux (`tmux attach -t
   desktop-commander`).

4. **Connect ChatGPT.** In the ChatGPT web app, configure the **Remote Desktop
   Commander plugin** using the bridge's connection information and the plugin's
   instructions. Your account must have access to the plugin; this kit does not
   install or configure it.

5. **Paste the banner's instruction into the conversation.** It tells ChatGPT to
   read [bootstrap/PROMPT.md](bootstrap/PROMPT.md) at its actual absolute VM path.
   Nothing is sent automatically. ChatGPT acknowledges the instructions and waits
   without starting tasks or changing the environment. Explicitly request a
   read-only connectivity/prerequisite check if wanted.

6. **Give ChatGPT a task.** Supply your existing project's absolute VM path, the
   desired change, and acceptance checks. Optionally adapt a [project template](#project-templates)
   as the project's root `AGENTS.md`, reconciling existing instructions before
   replacing them. ChatGPT reads those instructions, delegates work to Codex, and
   reviews the result. Keep the existing project directory; do not initialize Git
   for this setup. Check task output and exit status; START/END notifications are
   best effort.

For runner commands and notification examples, see
[bootstrap/PROMPT.md](bootstrap/PROMPT.md). Confirm absolute bundle and project
paths on the VM before running tasks; ChatGPT's local sandbox paths are separate.
