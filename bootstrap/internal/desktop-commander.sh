#!/usr/bin/env bash
# Preserve the installed service and foreground entry points.
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
exec python3 -B "$script_dir/bridge.py" "$BASH" "$@"
