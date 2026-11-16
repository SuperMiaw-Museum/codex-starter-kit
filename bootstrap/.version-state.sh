# Private installation receipts; sourced only by setup and the reminder.
version_state() {
  version_dir=${XDG_STATE_HOME:-$HOME/.local/state}/codex-starter-kit
  umask 077
  if [[ ${1:-} == create ]]; then
    mkdir -p -- "$(dirname -- "$version_dir")" || return 1
    mkdir -m 700 -- "$version_dir" 2>/dev/null || true
  fi
  [[ -d $version_dir && ! -L $version_dir && -O $version_dir && $(stat -c '%a' -- "$version_dir") == 700 ]]
}
record_version() {
  local component=$1 version temp
  version=$(cat "$script_dir/version.txt") || return 1
  [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  version_state create || return 1
  temp=$(mktemp "$version_dir/.receipt.XXXXXX") || return 1
  printf '%s\n' "$version" > "$temp"
  mv -fT -- "$temp" "$version_dir/$component"
}
