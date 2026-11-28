# Playground — Conditional instructions for ChatGPT

## Activation

Load this skill only when explicitly requested or approved by the human.

Confirm the following information unless already provided:

- SSH destination accessible from Codex Worker.
- Whether sudo is authorized on the playground.

Activation does not authorize unrelated tasks.

## Safety guidelines

The playground is a dedicated, isolated environment for experimentation and testing.

- Keep sensitive data and production credentials off the playground.
- Use existing SSH access and keep host verification enabled.
- Never use the playground to bypass worker restrictions.
- Keep privileged operations within the authorized playground and task scope.
- Do not clean up playground resources unless requested by the human.

## Usage

Include the SSH destination, sudo authorization and task scope in the Codex handoff.

Codex may use the playground when useful for the task, including installing dependencies, running Docker containers, building test environments and performing integration or end-to-end tests.

Codex must verify access before use and report any access or permission issues.

Report the resources created on the playground so they can be cleaned up later if requested.
