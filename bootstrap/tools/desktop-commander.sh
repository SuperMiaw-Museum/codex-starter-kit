#!/usr/bin/env bash
# Invoked by the optional user service; keep tmux available for manual inspection.
set -eo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [[ ${1:-} == --inside-tmux ]]; then
  trap 'tmux wait-for -S desktop-commander-exit' EXIT
  source "$script_dir/../.common-env.sh"
  npx @wonderwhy-er/desktop-commander@latest remote
else
  printf -v inner '%q --inside-tmux' "$script_dir/desktop-commander.sh"
  tmux new-session -d -s desktop-commander "$inner"
  tmux wait-for desktop-commander-exit
fi
