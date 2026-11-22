#!/usr/bin/env bash
# Check the interpreter before delegating the read-only report.
profile=${1:-all}
case $profile in
  hook|service|tools|all) ;;
  *) echo 'Usage: check-requirements.sh [hook|service|tools|all]' >&2; exit 2 ;;
esac
python=$(type -P python3) || {
  printf 'Requirements profile: %s\n' "$profile"
  color= reset=
  if [[ -t 1 ]]; then color=$'\033[31m'; reset=$'\033[0m'; fi
  printf '%s❌ KO  %s python3: missing from PATH; provide it before setup\nSummary: 0 OK, 1 KO\n' "$color" "$reset"
  exit 1
}
[[ $python == /* ]] || python=$PWD/$python
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
case $profile in service|tools|all) source "$script_dir/internal/.common-env.sh" ;; esac
exec "$python" -B "$script_dir/internal/check_requirements.py" "$profile"
