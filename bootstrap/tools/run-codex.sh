#!/usr/bin/env bash
# Usage: run-codex.sh PROJECT_DIR [--resume SESSION_ID] TASK_FILE
# Or:   run-codex.sh PROJECT_DIR [--resume SESSION_ID] --task "task instructions"
set -o pipefail
usage() {
  echo 'Usage: run-codex.sh PROJECT_DIR [--resume SESSION_ID] (TASK_FILE | --task "instructions")' >&2
  exit 2
}
[[ $# -ge 2 ]] || usage
project=$1
shift
resume_id=
if [[ $1 == --resume ]]; then
  [[ $# -ge 3 ]] || usage
  if [[ ! $2 =~ ^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$ ]]; then
    echo 'Session ID must be a UUID' >&2
    exit 2
  fi
  resume_id=$2
  shift 2
fi
if [[ $1 == --task ]]; then
  [[ $# -eq 2 ]] || usage
else
  [[ $# -eq 1 && $1 != --resume ]] || usage
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 2
source "$script_dir/../.common-env.sh"
project_dir=$(cd -- "$project" && pwd -P) || exit 2
if [[ $1 == --task ]]; then
  task=$2
  notice_task=$task
else
  [[ -f $1 && -r $1 ]] || { echo 'Task file must be readable' >&2; exit 2; }
  # A sentinel keeps command substitution from stripping the prompt's final newlines.
  notice_task=$(cat -- "$1" && printf '.') || exit 2
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
if [[ ${CODEX_STARTER_KIT_LIVE_INSPECT:-0} == 1 ]]; then
  command -v tmux >/dev/null || { echo 'CODEX_STARTER_KIT_LIVE_INSPECT=1 requires tmux in PATH' >&2; exit 127; }
fi
notify_on_exit() {
  local result=$?
  trap - EXIT HUP INT TERM
  if [[ -n $inspect_dir ]]; then
    tmux -S "$inspect_dir/socket" kill-server 2>/dev/null || true
    rm -rf -- "$inspect_dir"
  fi
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
inspect_dir=
trap notify_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Failure to notify must not prevent the requested work.
inspect_notice=
if [[ ${CODEX_STARTER_KIT_LIVE_INSPECT:-0} == 1 ]]; then
  inspect_dir=$(mktemp -d /tmp/codex-inspect.XXXXXXXX) || exit 1
  inspect_session=${inspect_dir##*/}
  inspect_session=${inspect_session/./-}
  printf -v inspect_notice 'tmux -S %q attach -r -t %q' "$inspect_dir/socket" "$inspect_session"
fi
start_args=(start "$(basename -- "$project_dir")" "$notice_task" "$$")
[[ -z $resume_id ]] || start_args+=("${resume_id,,}")
token=$(CODEX_STARTER_KIT_SHOW_FULL_PROMPT="${CODEX_STARTER_KIT_SHOW_FULL_PROMPT:-0}" \
  CODEX_STARTER_KIT_INSPECT_COMMAND="$inspect_notice" \
  "$script_dir/notify-task-done.sh" "${start_args[@]}") || token=
args=(exec)
[[ -z $resume_id ]] || args+=(resume)
help_args=("${args[@]}" --help)
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  args+=(--skip-git-repo-check)
fi
if [[ ${CODEX_STARTER_KIT_NOTIFY_METADATA:-1} == 1 ]]; then
  if [[ $("$codex_bin" "${help_args[@]}" 2>/dev/null) =~ [[:space:]]--json([[:space:]]|$) ]]; then
    metadata=$(mktemp /tmp/codex-starter-kit-notice.XXXXXXXX) || metadata=
    [[ -z $metadata ]] || args+=(--json)
  fi
fi
[[ -z $resume_id ]] || args+=("$resume_id")
args+=(-)
run_codex() {
  local parser=$1 metadata=$2 session_output=$3
  shift 3
  if [[ -n $metadata ]]; then
    "$@" | python3 "$parser" "$metadata" "$session_output"
    return "${PIPESTATUS[0]}"
  fi
  "$@"
}
if [[ ${CODEX_STARTER_KIT_LIVE_INSPECT:-0} == 1 ]]; then
  printf '%s\n' "$task" > "$inspect_dir/task" || exit 1
  export -f run_codex
  if ! tmux -S "$inspect_dir/socket" -f /dev/null new-session -d -s "$inspect_session" \
      -x 120 -y 40 -c "$project_dir" bash -c '
        inspect_dir=$1
        shift
        run_codex "$@" < "$inspect_dir/task"
        result=$?
        printf "%s\n" "$result" > "$inspect_dir/status"
      ' bash "$inspect_dir" "$script_dir/codex_notice.py" "$metadata" \
      "$inspect_dir/session-id" "$codex_bin" "${args[@]}"; then
    echo 'Could not start the tmux inspection session' >&2
    exit 1
  fi
  printf 'SANDBOX_HANDOFF_TMUX_SESSION=%s\n' "$inspect_session"
  printf 'Read-only inspection: tmux -S %q attach -r -t %q\n' "$inspect_dir/socket" "$inspect_session"
  while tmux -S "$inspect_dir/socket" has-session -t "$inspect_session" 2>/dev/null; do
    sleep 0.2
  done
  if [[ -f $inspect_dir/session-id ]]; then
    cat -- "$inspect_dir/session-id"
  fi
  if [[ ! -f $inspect_dir/status ]] || ! read -r result < "$inspect_dir/status"; then
    echo 'The tmux inspection session ended before reporting a Codex exit code' >&2
    exit 1
  fi
  exit "$result"
fi
run_codex "$script_dir/codex_notice.py" "$metadata" '' "$codex_bin" "${args[@]}" <<< "$task"
exit "$?"
