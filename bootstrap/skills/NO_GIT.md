# Non-Git workspace — conditional instructions for ChatGPT

Activate this skill when the human explicitly identifies the current workspace
as intentionally non-Git or requests NO_GIT. Scope it to the current conversation.

- Do not search for Git repositories or metadata in any workspace or its parents.
- Do not run Git commands, initialize Git, or suggest initializing a repository,
  executing commits, or pushing during this conversation.
- Commit message suggestions remain allowed. Base them on actual changes
  established by conversation context, task reports, supplied diffs, or direct
  file inspection. Ask for clarification only when evidence is insufficient.
- Inspect files directly to assess project status, changes, and remaining work.
  Report when no baseline is available rather than inventing change history.
- Carry these constraints into delegated Codex tasks.
- Do not treat the absence of Git as a blocker.

Keep these instructions active throughout the current conversation, including
workspace changes, until the human explicitly deactivates the skill.
Do not carry this activation into other conversations.
This skill does not override authorization or isolation requirements.
