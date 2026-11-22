#!/usr/bin/env bash
# Keep the release CLI at its original path.
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
exec python3 -B "$script_dir/release.py" "$@"
