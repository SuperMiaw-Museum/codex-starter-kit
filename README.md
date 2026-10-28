# codex-starter-kit

Reusable project instructions and a remote workflow: ChatGPT coordinates work
through Remote Desktop Commander; Codex CLI edits and checks your existing project
on a Linux VM. You review the results.

## Getting started

1. **Prepare an isolated Linux VM.** Use a nonprivileged account without
   administrative access. Install Bash, Git, Python 3, Node.js with npm/npx,
   Codex CLI, and your project's tools; complete Codex sign-in as that account.
   Before connecting,
   configure a firewall **outside the VM** to block access to the LAN, host, and
   management services while permitting necessary Internet access and any trusted
   administration access. Keep a VM console available.

2. **Clone this starter kit on the VM.** In the nonprivileged account's
   terminal, replace the URL below with this repository's clone URL:

   ```bash
   git clone <starter-kit-repository-url> ~/codex-starter-kit
   ```

3. **Manually start the bridge on the VM.** Run this in the VM terminal
   **before ChatGPT can access the VM remotely**:

   ```bash
   ~/codex-starter-kit/bootstrap/remote-control.sh
   ```

   Keep it running. It prints the prompt and connection information; the first run
   may download the bridge. ChatGPT cannot perform this initial startup for you.

4. **Connect ChatGPT.** In the **ChatGPT web app**, connect/configure the
   **Remote Desktop Commander plugin** using the bridge's connection information
   and the plugin's instructions. You need an account where the plugin is available;
   this kit does not install or configure it. Keep connection tokens private.

5. **Paste the instructions.** Copy the contents of
   [bootstrap/PROMPT.md](bootstrap/PROMPT.md) into that ChatGPT conversation (also
   printed by the launcher). This initializes working instructions only:
   **ChatGPT acknowledges and waits; it does not start tasks or change the environment.**
   You may explicitly request a read-only connectivity/prerequisite check.

6. **Begin work in an existing source project.** Give ChatGPT the project's
   absolute VM path, desired change, and acceptance checks. Optionally copy and
   adapt a suitable [project template](templates/)
   into that project's root as `AGENTS.md`; reconcile any
   existing instructions before replacing them. ChatGPT reads the project
   instructions, delegates requested work to Codex, and reviews the result.
   Keep the project in its existing directory; do not initialize Git for this setup.
   Review changes before committing, publishing, or deploying.

**Security:** use a disposable VM and backups. Keep production credentials,
personal files, and host mounts out of it. NAT and Codex's workspace sandbox do
not replace VM/network isolation. The human manages firewall policy; agents must
not weaken it. Remote commands can affect anything the account can access.

## Project templates

- [FastAPI backend](templates/fastapi-backend/AGENTS.md)
- [Alpine frontend](templates/alpine-frontend/AGENTS.md)
- [Python CLI](templates/python-cli/AGENTS.md)
- [Debian script](templates/script-debian/AGENTS.md)
- [FreeBSD script](templates/script-freebsd/AGENTS.md)
- [Legacy project](templates/legacy/AGENTS.md)

## Optional personal workflow

One example is to keep Codex in an isolated VM and use rsync over SSH from a
trusted host to exchange project files, excluding secrets and local configuration.
Keep Git operations and final review/integration on the trusted host. Transfers
should use the trusted administration access allowed by the firewall policy.
This is an optional example, not a requirement for using this kit.

## Reference

[bootstrap/PROMPT.md](bootstrap/PROMPT.md) contains operational instructions,
runner usage, and notification examples. Adapt its home-relative example paths
to the VM and explicitly confirm the resolved absolute bundle and project paths
before running tasks. ChatGPT's local sandbox paths are separate from VM paths.

The runner sends **START** and **END** terminal notifications automatically;
END includes status, duration, and a best-effort bell in eligible terminals
(including SSH). Delivery depends on Python 3, accessible same-UID terminals
enumerated under `/dev/pts`, and client settings. Always check task output and exit status.

## License and disclaimer

[Unlicense](LICENSE): public domain, provided **as is**, without warranty.
This personal, opinionated workflow is an example to adapt to your environment;
you remain responsible for its use and for reviewing changes.
