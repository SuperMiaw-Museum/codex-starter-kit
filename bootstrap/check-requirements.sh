#!/usr/bin/env bash
# Read-only prerequisite report. Never installs or downloads dependencies.
set -uo pipefail
profile=${1:-all}
case $profile in hook|service|tools|all) ;; *) echo 'Usage: check-requirements.sh [hook|service|tools|all]' >&2; exit 2 ;; esac
ok=0 ko=0
color= reset=
[[ ! -t 1 ]] || reset=$'\033[0m'
report() {
  local status=$1 label=$2 detail=$3 icon
  case $status in
    OK) icon='✅'; color=$'\033[32m'; ((ok+=1)) ;;
    KO) icon='❌'; color=$'\033[31m'; ((ko+=1)) ;;
  esac
  [[ -n $reset ]] || color=
  printf '%s%s %-4s%s %s: %s\n' "$color" "$icon" "$status" "$reset" "$label" "$detail"
}
need() {
  local name=$1 group=$2
  [[ $group == common || $profile == all || $profile == "$group" ]] || return 0
  if command -v "$name" >/dev/null 2>&1; then report OK "$name" available
  else report KO "$name" 'missing from PATH; provide it before setup'; fi
}
printf 'Requirements profile: %s\n' "$profile"
need bash common
need dirname common
need cat common
for name in mkdir stat mktemp mv rm chmod cp cmp; do
  if [[ $profile == hook || $profile == service || $profile == all ]]; then need "$name" common
  fi
done
need python3 tools
need awk hook
need flock hook
need readlink hook
if [[ $profile == hook || $profile == tools || $profile == all ]]; then
  if [[ -r /proc/self/comm && -r /proc/self/status && -r /proc/self/stat && -r /proc/self/cmdline && -e /proc/self/exe && -r /proc/sys/kernel/random/boot_id ]]; then
    report OK /proc 'Linux process and boot information readable'
  else report KO /proc 'readable Linux procfs required'; fi
fi
if [[ $profile == service || $profile == tools || $profile == all ]]; then
  # Match runtime NVM resolution without changing the caller environment.
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
  source "$script_dir/.common-env.sh"
fi
need tmux service
# The generated unit uses fixed executable paths.
if [[ $profile == service || $profile == all ]]; then
  for path in /usr/bin/bash /usr/bin/tmux; do
    if [[ -x $path ]]; then report OK "$path" executable; else report KO "$path" 'required by generated unit'; fi
  done
fi
need node service
need npx service
need systemctl service
if [[ $profile == service || $profile == all ]]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    report OK 'systemctl --user' 'user manager reachable'
  else report KO 'systemctl --user' 'user manager unavailable'; fi
fi
need git tools
need codex tools
need basename tools
printf 'Summary: %d OK, %d KO\n' "$ok" "$ko"
((ko == 0))
