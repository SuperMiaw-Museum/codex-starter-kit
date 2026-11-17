#!/usr/bin/env bash
# Launch the bridge in the current terminal or in the service's tmux session.
set -eo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
bridge=(npx @wonderwhy-er/desktop-commander@latest remote)
if [[ ${1:-} == --foreground ]]; then
  # Lock the checkout directory inode without creating a lock file.
  exec 9<"$script_dir/.."
  if ! flock -n 9; then
    printf 'Could not lock this checkout. A one-shot bridge may already be starting or running.\n' >&2
    exit 1
  fi
  service_status=$(systemctl --user is-active desktop-commander.service 2>/dev/null) || true
  case $service_status in
    active|activating|reloading|deactivating|refreshing)
      printf 'desktop-commander.service is %s. Stop it before starting a one-shot bridge.\n' "$service_status" >&2
      exit 1 ;;
  esac
  if tmux has-session -t '=desktop-commander' 2>/dev/null; then
    printf 'The desktop-commander tmux session already exists. Use or stop it first.\n' >&2
    exit 1
  fi
  source "$script_dir/../.common-env.sh"
  "$script_dir/../remote-control.sh" --show
  exec "${bridge[@]}"
elif [[ ${1:-} == --inside-tmux ]]; then
  trap 'tmux wait-for -S desktop-commander-exit' EXIT
  source "$script_dir/../.common-env.sh"
  "${bridge[@]}"
else
  printf -v inner '%q --inside-tmux' "$script_dir/desktop-commander.sh"
  tmux new-session -d -s desktop-commander "$inner"
  tmux wait-for desktop-commander-exit
fi
