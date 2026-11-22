#!/usr/bin/env bash
# Keep the installed bootstrap entry point and diagnose a missing interpreter.
case ${1:---preview} in
  --preview|--install-hook|--install-service) ;;
  *) printf 'Usage: %s [--preview|--install-hook|--install-service]\n' "$0" >&2; exit 2 ;;
esac
if ! type -P python3 >/dev/null 2>&1; then
  echo 'Setup requires python3; provide it before setup.' >&2
  exit 1
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
exec python3 -B "$script_dir/internal/setup_bootstrap.py" "$BASH" "${1:---preview}"
