#!/usr/bin/env bash
set -eo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$script_dir/.common-env.sh"
cat -- "$script_dir/PROMPT.md"
printf '\n'
if ! command -v npx >/dev/null 2>&1; then
  printf 'npx not found; install Node.js (or configure NVM).\n' >&2
  exit 127
fi
exec npx --yes @wonderwhy-er/desktop-commander@latest remote "$@"
