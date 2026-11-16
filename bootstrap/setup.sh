#!/usr/bin/env bash
# codex-starter-kit version: 1.0.0
# Explicit, user-invoked setup only. With no options, preview the managed unit.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$script_dir/.version-state.sh"
unit_path=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/desktop-commander.service
render_unit() {
  # systemd quoted arguments: escape backslashes, quotes, specifiers and env syntax.
  local runner=$script_dir/tools/desktop-commander.sh home=$HOME
  runner=${runner//\\/\\\\}; runner=${runner//\"/\\\"}; runner=${runner//%/%%}; runner=${runner//\$/\$\$}
  home=${home//\\/\\\\}; home=${home//\"/\\\"}; home=${home//%/%%}
  printf '%s\n' '# codex-starter-kit managed unit v1' '[Unit]' 'Description=Desktop Commander Remote' 'After=network-online.target' 'Wants=network-online.target' '' '[Service]' 'Type=simple'
  printf 'Environment="HOME=%s"\nExecStart=/usr/bin/bash "%s"\n' "$home" "$runner"
  printf '%s\n' 'ExecStop=-/usr/bin/tmux kill-session -t desktop-commander' 'Restart=always' 'RestartSec=15' '' '[Install]' 'WantedBy=default.target'
}
case ${1:---preview} in
  --preview) result=0; "$script_dir/check-requirements.sh" service || result=$?; render_unit; printf '\nTarget: %s\nRun --install-hook or --install-service explicitly to apply.\n' "$unit_path"; exit "$result" ;;
  --install-hook)
    "$script_dir/check-requirements.sh" hook
    # Refuse symlinks to avoid replacing an unexpected target.
    rc=$HOME/.bashrc
    [[ ! -L $rc && ( ! -e $rc || -f $rc ) ]] || { echo 'Refusing non-regular .bashrc' >&2; exit 1; }
    begin='# BEGIN codex-starter-kit reminder'
    end='# END codex-starter-kit reminder'
    temp=$(mktemp "$HOME/.bashrc.codex.XXXXXX")
    trap 'rm -f -- "$temp"' EXIT
    if [[ -e $rc ]]; then
      # Abort on malformed managed blocks instead of discarding user content.
      awk -v b="$begin" -v e="$end" '
        $0==b {if (inside) exit 2; inside=1; next}
        $0==e {if (!inside) exit 2; inside=0; next}
        !inside {print}
        END {if (inside) exit 2}' "$rc" > "$temp"
      chmod --reference="$rc" "$temp"
    fi
    printf '%s\n' "$begin" >> "$temp"
    printf -v reminder '%q' "$script_dir/remote-control.sh"
    printf 'case $- in *i*) /usr/bin/bash %s --auto ;; esac\n%s\n' "$reminder" "$end" >> "$temp"
    # Avoid accumulating blank lines on repeated setup.
    if [[ -f $rc ]] && cmp -s "$temp" "$rc"; then
      echo 'Reminder hook already current.'
    else
      mv -- "$temp" "$rc"
      echo 'Reminder hook installed in ~/.bashrc (takes effect in a new interactive Bash).'
    fi
    record_version hook ;;
  --install-service)
    "$script_dir/check-requirements.sh" service
    mkdir -p -- "$(dirname -- "$unit_path")"
    [[ ! -L $unit_path && ( ! -e $unit_path || -f $unit_path ) ]] || { echo 'Refusing non-regular service file' >&2; exit 1; }
    temp=$(mktemp "$(dirname -- "$unit_path")/.desktop-commander.XXXXXX")
    trap 'rm -f -- "$temp"' EXIT
    render_unit > "$temp"
    if [[ -f $unit_path ]] && cmp -s "$temp" "$unit_path"; then
      echo 'Service content already current.'
    else
      [[ ! -f $unit_path ]] || cp -p -- "$unit_path" "$temp.previous"
      if [[ -f $temp.previous ]]; then mv -- "$temp.previous" "$unit_path.backup"; fi
      mv -- "$temp" "$unit_path"
      systemctl --user daemon-reload
      echo 'Service unit installed/updated; daemon reloaded.'
    fi
    echo 'No service was started, stopped, restarted, or enabled.'
    echo 'When ready to disconnect/reconnect manually: systemctl --user restart desktop-commander.service'
    echo 'Optional boot/login activation: systemctl --user enable desktop-commander.service'
    record_version service ;;
  *) echo "Usage: $0 [--preview|--install-hook|--install-service]" >&2; exit 2 ;;
esac
