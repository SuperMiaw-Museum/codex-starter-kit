#!/usr/bin/env bash
# Preserve the standalone ancestry check.
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
exec python3 -B "$script_dir/agent_ancestry.py" "$@"
