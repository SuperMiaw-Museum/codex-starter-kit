#!/usr/bin/env bash
# Usage: run-codex.sh PROJECT_DIR TASK_FILE
# Or:   run-codex.sh PROJECT_DIR --task "task instructions"
set -o pipefail
if ! { [[ $# -eq 2 ]] || { [[ $# -eq 3 ]] && [[ $2 == --task ]]; }; }; then
  echo 'Usage: run-codex.sh PROJECT_DIR TASK_FILE | PROJECT_DIR --task "instructions"' >&2
  exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 2
source "$script_dir/../.common-env.sh"
project_dir=$(cd -- "$1" && pwd -P) || exit 2
if [[ $2 == --task && $# -eq 3 ]]; then
  task=$3
else
  [[ -f $2 && -r $2 ]] || { echo 'Task file must be readable' >&2; exit 2; }
  task=$(cat -- "$2") || exit 2
fi
[[ -n $task ]] || { echo 'Task must not be empty' >&2; exit 2; }
codex_bin=$(type -P codex || true)
[[ -n $codex_bin ]] || { echo 'Codex not found in PATH' >&2; exit 127; }
codex_bin=$(cd -- "$(dirname -- "$codex_bin")" && printf '%s/%s' "$PWD" "$(basename -- "$codex_bin")") || exit 127
export PATH="$(dirname -- "$codex_bin"):$PATH"
cd -- "$project_dir" || exit 2
notify_on_exit() {
  local result=$?
  trap - EXIT HUP INT TERM
  if [[ -n $token ]]; then
    "$script_dir/notify-task-done.sh" end "$token" "$result" || true
  fi
  printf 'SANDBOX_HANDOFF_EXIT_CODE=%s\n' "$result"
  exit "$result"
}
token=
trap notify_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Failure to notify must not prevent the requested work.
token=$("$script_dir/notify-task-done.sh" start "$(basename -- "$project_dir")" "$task" "$$") || token=
args=(exec)
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  args+=(--skip-git-repo-check)
fi
"$codex_bin" "${args[@]}" - <<< "$task"
exit "$?"
