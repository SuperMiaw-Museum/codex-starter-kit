#!/usr/bin/env bash
# codex-starter-kit version: 2.0.0
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec python3 "$script_dir/scripts/release.py" "$@"
