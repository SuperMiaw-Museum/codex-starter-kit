#!/usr/bin/env bash
# start PROJECT LABEL prints only the random token on stdout; notices use stderr.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
case ${1:-} in
  start) [[ $# -eq 3 ]] || exit 2 ;;
  end) [[ $# -eq 3 ]] || exit 2 ;;
  status) [[ $# -eq 1 ]] || exit 2 ;;
  *) echo 'Usage: manual-task.sh start PROJECT LABEL | end TOKEN EXIT_CODE | status' >&2; exit 2 ;;
esac
exec "$script_dir/notify-task-done.sh" "$@"
