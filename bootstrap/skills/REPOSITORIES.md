# External repository access — conditional instructions for ChatGPT

Under the agent account's home, `~/.codex-starter-kit-repos-ro/` and
`~/.codex-starter-kit-repos-rw/` are reserved optional directories containing
symbolic links to external Git repositories. For task-relevant linked repositories,
`ro` permits inspection only; `rw` permits inspection and editing within the
requested scope. Resolve links to identify the repository: if the same repository
appears in both directories, treat it as read-only and report the conflict.
Neither directory grants blanket access to unrelated repositories. Normal Git,
destructive-operation, and remote-operation authorization rules still apply;
`rw` alone does not authorize commits, pushes, publishing, or destructive actions.
This is a best-effort behavioral convention, not a security boundary or filesystem
enforcement. Do not create or change these directories or links unless requested.
