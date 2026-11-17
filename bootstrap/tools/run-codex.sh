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
  notice_task=$task
else
  [[ -f $2 && -r $2 ]] || { echo 'Task file must be readable' >&2; exit 2; }
  # A sentinel keeps command substitution from stripping the prompt's final newlines.
  notice_task=$(cat -- "$2" && printf '.') || exit 2
  notice_task=${notice_task%.}
  task=${notice_task%"${notice_task##*[!$'\n']}"}
  if [[ ${CODEX_STARTER_KIT_SHOW_FULL_PROMPT:-0} != 1 ]]; then
    notice_task=$task
  fi
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
    local end_args=(end "$token" "$result")
    [[ -z $metadata ]] || end_args+=("$metadata")
    "$script_dir/notify-task-done.sh" "${end_args[@]}" || true
  fi
  [[ -z $metadata ]] || rm -f -- "$metadata"
  printf 'SANDBOX_HANDOFF_EXIT_CODE=%s\n' "$result"
  exit "$result"
}
token=
metadata=
trap notify_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Failure to notify must not prevent the requested work.
token=$(CODEX_STARTER_KIT_SHOW_FULL_PROMPT="${CODEX_STARTER_KIT_SHOW_FULL_PROMPT:-0}" \
  "$script_dir/notify-task-done.sh" start "$(basename -- "$project_dir")" "$notice_task" "$$") || token=
args=(exec)
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  args+=(--skip-git-repo-check)
fi
if [[ ${CODEX_STARTER_KIT_NOTIFY_METADATA:-1} == 1 && -n $token ]]; then
  if [[ $("$codex_bin" exec --help 2>/dev/null) =~ [[:space:]]--json([[:space:]]|$) ]]; then
    metadata=$(mktemp /tmp/codex-starter-kit-notice.XXXXXXXX) || metadata=
  fi
fi
if [[ -n $metadata ]]; then
  "$codex_bin" "${args[@]}" --json - <<< "$task" |
    python3 "$script_dir/codex_notice.py" "$metadata"
  exit "${PIPESTATUS[0]}"
fi
"$codex_bin" "${args[@]}" - <<< "$task"
exit "$?"
