#!/usr/bin/env bash
# Usage: run-codex.sh PROJECT_DIR "task instructions"
set -o pipefail
if (( $# < 2 )); then
  printf 'Usage: %s PROJECT_DIR "task instructions"\n' "$0" >&2
  exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 2
source "$script_dir/../.common-env.sh"
project_dir=$1
shift
if [[ ! -d "$project_dir" ]]; then
  printf 'Directory not found: %s\n' "$project_dir" >&2
  exit 2
fi
project_dir=$(cd -- "$project_dir" && pwd -P) || exit 2
project=$(basename -- "$project_dir")
task=$*
codex_bin=$(type -P codex || true)
if [[ -z "$codex_bin" ]]; then
  printf 'Codex not found in PATH; install it in your active Node.js environment.\n' >&2
  exit 127
fi
# Resolve relative PATH entries before changing the working directory.
codex_bin=$(cd -- "$(dirname -- "$codex_bin")" && printf '%s/%s' "$PWD" "$(basename -- "$codex_bin")") || exit 127
export PATH="$(dirname -- "$codex_bin"):$PATH"
cd -- "$project_dir" || exit 2
args=(exec -s workspace-write)
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  args+=(--skip-git-repo-check)
fi
notify_on_exit() {
  local result=$?
  trap - EXIT HUP INT TERM
  "$script_dir/notify-task-done.sh" end "$project" "$result" "$task" "$start_token" || true
  exit "$result"
}
start_token=$(date +%s)
trap notify_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Keep a fallback token so notification failure cannot affect Codex execution.
notified_token=$("$script_dir/notify-task-done.sh" start "$project" "$task") && start_token=$notified_token
"$codex_bin" "${args[@]}" -- "$task" </dev/null
exit "$?"
