# Codex session resumption — conditional instructions for ChatGPT

When the human asks to continue or correct earlier Codex work, consider resuming
that Codex session rather than starting a new one. Use the runner's optional
session-resume argument only with an explicitly identified, valid session ID
from the relevant prior task; never guess an ID, resume an unrelated session,
or silently fall back to a fresh session if resumption fails. If the ID is
unknown, ask the human or use an explicitly authorized lookup of task metadata.
Treat a resumed session as potentially stale: supply a clear new task describing
current intent and instruct Codex to re-check repository state and AGENTS.md.
Do not resume a session with another active task, and do not infer permission to
commit, push, or edit additional repositories from session history. Keep the
same asynchronous handoff and START/END notification rules as fresh tasks.
