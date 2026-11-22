#!/usr/bin/env bash
# Load the user's toolchain before handing off to the Python supervisor.
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 2
codex_caller_dir=$PWD
source "$script_dir/../internal/.common-env.sh"
cd -- "$codex_caller_dir" || exit 2
exec python3 -B "$script_dir/../internal/batch.py" "$@"
