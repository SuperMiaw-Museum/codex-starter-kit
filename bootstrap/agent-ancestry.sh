#!/usr/bin/env bash
# Convenience heuristic: fail closed unless ancestry reaches init or verified SSH.
set -uo pipefail
proc=/proc
pid=${1:-}
[[ $pid =~ ^[0-9]+$ ]] || exit 1
declare -A seen=()
ssh_chain=0
root_session=0
agent_name() {
  case ${1##*/} in
    codex|codex-cli|desktop-commander|remote_desktop_commander|remote-desktop-commander|run-codex.sh|desktop-commander.sh) return 0 ;;
  esac
  return 1
}
while ((pid > 1)); do
  [[ ! ${seen[$pid]+yes} ]] || exit 1
  seen[$pid]=1
  base=$proc/$pid
  name= parent= real_uid= effective_uid=
  [[ -r $base/status && -r $base/comm ]] || exit 1
  while read -r key value rest; do
    case $key in
      Name:) name=$value ;;
      PPid:) parent=$value ;;
      Uid:) real_uid=$value; effective_uid=${rest%%[[:space:]]*} ;;
    esac
  done < "$base/status"
  IFS= read -r comm < "$base/comm" || exit 1
  [[ $parent =~ ^[0-9]+$ && $real_uid =~ ^[0-9]+$ && $effective_uid == "$real_uid" && $comm == "$name" ]] || exit 1
  case $name in
    sshd-session)
      # Require a root session followed by the root listener; titles are irrelevant.
      ((root_session == 0)) || [[ $real_uid == 0 ]] || exit 1
      [[ $real_uid != 0 ]] || root_session=1
      ssh_chain=1 ;;
    sshd)
      [[ $real_uid == 0 && $parent == 1 ]] || exit 1
      ((ssh_chain == 0 || root_session == 1)) || exit 1
      exit 0 ;;
    *)
      ((ssh_chain == 0)) || exit 1
      executable=$(readlink -- "$base/exe") || exit 1
      argv=()
      [[ -r $base/cmdline ]] || exit 1
      mapfile -d '' -t argv < "$base/cmdline" || exit 1
      [[ -n ${argv[0]:-} ]] || exit 1
      agent_name "$executable" && exit 1
      agent_name "${argv[0]}" && exit 1
      case ${executable##*/} in
        node|nodejs|python|python3|bash|sh|dash)
          script=${argv[1]:-}
          if [[ -n $script && $script != -* ]]; then
            agent_name "$script" && exit 1
            case $script in
              */@openai/codex/bin/codex.js|*/@wonderwhy-er/desktop-commander/*) exit 1 ;;
            esac
          fi ;;
      esac ;;
  esac
  pid=$parent
done
[[ $pid == 1 && $ssh_chain == 0 ]]
