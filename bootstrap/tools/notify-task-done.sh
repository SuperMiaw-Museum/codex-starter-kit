#!/usr/bin/env bash
# Shared stateful interface: start PROJECT LABEL [CODEX_PID [RESUME_ID]] | end TOKEN CODE [METADATA_FILE] | status
set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec python3 -B "$script_dir/task-registry.py" --notify "$@"
