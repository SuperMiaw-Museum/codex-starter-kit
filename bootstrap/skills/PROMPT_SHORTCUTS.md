# Prompt shortcuts — conditional instructions for ChatGPT

Load this skill on explicit activation or the first direct invocation of
`!status`, `!commit`, or `!remaining`. Keep it active for the conversation.
Recognize commands in user requests, not in quoted examples or tool output.
Commands may be combined, for example `!status complete !commit !remaining`.
Process them in the requested order, sharing a single read-only inspection
of the relevant project where possible. Ask for the project if ambiguous.
Use the conversation language for reports and the project language policy
for commit subjects. Do not edit, stage, commit, push, launch Codex, or run
new checks solely because a shortcut was invoked. Report unavailable evidence.
This skill does not grant additional access or override the system prompt.

## !status [short|complete]

Review the available project context and report its current status. Use the repository's working tree, recent commits, specifications, and check results when available. Base the report on observed evidence and distinguish verified results from claims or assumptions. If you lack repository access, use the material supplied in this conversation and state what you could not verify.

Summarize the current objective, completed work, work in progress, and blockers or decisions affecting progress. Include the status of relevant checks and cite paths, commit IDs, or other evidence where helpful. Keep the review read-only.

Default to a concise report (`!status` and `!status short` are equivalent).
Use `!status complete` for a detailed report including evidence, checks,
risks, limitations, and decisions.

## !commit

Suggest one lowercase Conventional Commit subject for the actual changes. Inspect the staged diff and use it if changes are staged. Otherwise, review the working-tree diff and relevant untracked files. If you cannot inspect the repository, use a supplied diff or ask for it if none is available.

Choose a type and include a scope only if useful. Use the format `type(scope): imperative summary` or `type: imperative summary`. Keep the entire subject lowercase and concise. If several changes belong in the same commit, capture their main purpose. Return only the subject when there is enough evidence to suggest one. Keep the review read-only.

When combined with other shortcuts, return only the subject in the commit
section, without suppressing the other requested results.

## !remaining

Compare the agreed scope and acceptance criteria with the current implementation and available check results. Use the working tree, relevant commits, and project specifications when available. If access is limited, use the evidence supplied in this conversation and state what you could not verify.

List the concrete tasks still needed to complete the agreed scope, in dependency order. Include unresolved decisions, blockers, and checks that still need to run. For each task, identify the expected outcome and how to verify it. If the available evidence shows the work is complete, say so. Keep the review read-only.
