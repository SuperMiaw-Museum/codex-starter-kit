#!/usr/bin/env bash
# Preserve the installed hook and manual entry point.
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
if [[ ${1:-} == --auto ]] && ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi
exec python3 -B "$script_dir/remote_control.py" "$0" "$@"
