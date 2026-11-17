#!/usr/bin/env bash
# codex-starter-kit version: 2.2.0
# SSH session banner and optional foreground bridge launcher.
set -eo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
show_reminder() {
  local accent= reset= version service_status
  if [[ -t 2 ]]; then accent=$'\033[1;36m'; reset=$'\033[0m'; fi
  version=$(cat "$script_dir/version.txt" 2>/dev/null) || version=unknown
  printf '\n%sCODEX STARTER KIT · %s%s\n\n' "$accent" "$version" "$reset" >&2
  service_status=$(systemctl --user is-active desktop-commander.service 2>/dev/null) || service_status=inactive
  [[ -n $service_status ]] || service_status=unknown
  printf 'Remote Desktop Commander: %s\n' "$service_status" >&2
  "$script_dir/tools/manual-task.sh" status >&2 || true
  printf '\n%s=== COPY THIS PROMPT INTO CHATGPT ===%s\n\nRead "%s/PROMPT.md" via Remote Desktop Commander, treat it as operational guidance for this session, acknowledge, and wait for my next request.\n\n' "$accent" "$reset" "$script_dir" >&2
}
case ${1:---show} in
  --show) show_reminder; exit ;;
  --once)
    [[ $# -eq 1 ]] || { printf 'Usage: %s --once\n' "$0" >&2; exit 2; }
    exec "$script_dir/tools/desktop-commander.sh" --foreground ;;
  --auto) ;;
  *) printf 'Usage: %s [--show|--auto|--once]\n' "$0" >&2; exit 2 ;;
esac
# The .bashrc hook calls this only from interactive Bash. Require a terminal too.
[[ -t 0 && -t 2 ]] || exit 0
# Fail closed on unreadable or unknown process ancestry.
bash "$script_dir/agent-ancestry.sh" "$PPID" || exit 0
show_reminder || true
# Compare installed component receipts on every interactive SSH login.
source "$script_dir/.version-state.sh"
checkout_version=$(cat "$script_dir/version.txt") || exit 0
if [[ $checkout_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && version_state; then
  pending_components=()
  pending_versions=()
  for component in hook service; do
    receipt=$version_dir/$component
    [[ -f $receipt && ! -L $receipt && -O $receipt ]] || continue
    installed=$(cat "$receipt") || continue
    [[ $installed =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && $installed != "$checkout_version" ]] || continue
    pending_components+=("$component")
    pending_versions+=("$installed")
  done
  if (( ${#pending_components[@]} > 0 )); then
    yellow= cyan= reset=
    if [[ -t 2 ]]; then
      yellow=$'\033[1;33m'
      cyan=$'\033[1;36m'
      reset=$'\033[0m'
    fi
    {
      printf '\n%s⚠️ CODEX STARTER KIT · Setup outdated%s\n' "$yellow" "$reset"
      for i in "${!pending_components[@]}"; do
        component=${pending_components[i]}
        installed=${pending_versions[i]}
        label=${component^}
        printf '\n🔧 %s: %s → %s\n' "$label" "$installed" "$checkout_version"
        printf '%s%s/setup.sh --install-%s%s\n' "$cyan" "$script_dir" "$component" "$reset"
      done
      printf '\n'
    } >&2
  fi
fi
