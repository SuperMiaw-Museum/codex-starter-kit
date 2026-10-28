#!/usr/bin/env bash
set -eo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$script_dir/.common-env.sh"
banner_style=''
banner_reset=''
if [[ -t 2 && -z ${NO_COLOR:-} && ${TERM:-dumb} != dumb ]]; then
  banner_style=$'\033[1;36m'
  banner_reset=$'\033[0m'
fi
printf '\n%s=== COPY INTO CHATGPT AFTER CONNECTING ===%s\n' "$banner_style" "$banner_reset" >&2
printf 'Copy/paste the instruction below; it is not sent automatically.\n\n' >&2
printf 'Read "%s/PROMPT.md" via Remote Desktop Commander, treat it as operational guidance for this session, acknowledge, and wait for my next request.\n\n' "$script_dir" >&2
if ! command -v npx >/dev/null 2>&1; then
  printf 'npx not found; install Node.js (or configure NVM).\n' >&2
  exit 127
fi
exec npx --yes @wonderwhy-er/desktop-commander@latest remote "$@"
