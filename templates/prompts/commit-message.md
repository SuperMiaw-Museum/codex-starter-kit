# Commit message

Suggest one lowercase Conventional Commit subject for the actual changes. Inspect the staged diff and use it if changes are staged. Otherwise, review the working-tree diff and relevant untracked files. If you cannot inspect the repository, use a supplied diff or ask for it if none is available.

Choose a type and include a scope only if useful. Use the format `type(scope): imperative summary` or `type: imperative summary`. Keep the entire subject lowercase and concise. If several changes belong in the same commit, capture their main purpose. Return only the subject when there is enough evidence to suggest one. Keep the review read-only.
