#!/usr/bin/env bash
# start PROJECT LABEL prints only the random token on stdout; notices use stderr.
set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec python3 -B "$script_dir/../internal/task_registry_cli.py" --manual "$@"
