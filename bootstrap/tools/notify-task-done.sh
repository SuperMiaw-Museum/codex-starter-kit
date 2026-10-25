#!/usr/bin/env bash
# Legacy usage: notify-task-done.sh [project] [exit-code] [task] [started] [finished] [elapsed-seconds] [START|END]
# Best-effort delivery to this user's logged-in terminals, including SSH.
# Explicit interface:
#   start PROJECT TASK                 -> epoch token on stdout, notification on stderr
#   end PROJECT EXIT_CODE TASK TOKEN   -> notification on stdout
# Tokens are epoch seconds, retained by the caller; no shared state files.
deliver() {
phase=${7:-END}
case "$phase" in
  START|END) ;;
  *) printf 'Invalid notification phase: %s\n' "$phase" >&2; return 2 ;;
esac
if ! command -v python3 >/dev/null 2>&1; then
  local timestamp task status=OK seconds=0 hours minutes indicator
  timestamp=$(date -d "${5:-${4:-now}}" '+%H:%M:%S' 2>/dev/null) || timestamp=$(date '+%H:%M:%S')
  task=${3:-}
  task=${task//$'\r'/ }
  task=${task//$'\n'/ }
  task=${task//$'\t'/ }
  if (( ${#task} > 120 )); then
    task="${task:0:117}..."
  fi
  [[ "${2:-0}" == 0 ]] || status="ERROR ${2}"
  indicator=START
  [[ "$phase" == START ]] || indicator="END ($status)"
  printf '[%s] Codex [%s] %s — %s' "$timestamp" "${1:-Codex}" "$indicator" "$task"
  if [[ "$phase" == END ]]; then
    [[ "${6:-}" =~ ^[0-9]{1,12}$ ]] && seconds=$((10#$6))
    hours=$((seconds / 3600))
    minutes=$((seconds % 3600 / 60))
    printf ' Duration: %02dh %02dm %02ds' "$hours" "$minutes" "$((seconds % 60))"
  fi
  printf '\n'
  return 0
fi
python3 - "$@" <<'PY'
import os
from datetime import datetime
import re
import stat
import sys


def clean(value, limit):
    value = re.sub(r"[\r\n\t]", " ", value)
    return "".join(c for c in value if c.isprintable())[:limit]


project = clean(sys.argv[1] if len(sys.argv) > 1 else "Codex", 80)
result = clean(sys.argv[2] if len(sys.argv) > 2 else "0", 32)
task = clean(sys.argv[3] if len(sys.argv) > 3 else "", sys.maxsize)
if len(task) > 120:
    task = task[:117] + "..."
status = "OK" if result == "0" else f"ERROR {result}"
phase = sys.argv[7] if len(sys.argv) > 7 else "END"
indicator = "START" if phase == "START" else f"END ({status})"
time_arg = 4 if phase == "START" else 5
timestamp = datetime.now().astimezone()
if len(sys.argv) > time_arg and sys.argv[time_arg]:
    try:
        timestamp = datetime.strptime(sys.argv[time_arg], "%Y-%m-%d %H:%M:%S %z").astimezone()
    except ValueError:
        pass
prefix = f"[{timestamp:%H:%M:%S}] Codex [{project}] "
message = prefix + indicator + f" — {task}"
if phase == "END":
    seconds = sys.argv[6] if len(sys.argv) > 6 else ""
    seconds = int(seconds) if re.fullmatch(r"[0-9]{1,12}", seconds) else 0
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    message += f" Duration: {hours:02d}h {minutes:02d}m {seconds:02d}s"
print(message, flush=True)
bell = "\a" if phase == "END" else ""
color = "36" if phase == "START" else "32" if result == "0" else "31"
terminal_message = (prefix + f"\033[{color}m{indicator}\033[0m"
                    + message[len(prefix) + len(indicator):])
payload = f"\r\n{bell}{terminal_message}\r\n".encode()
try:
    uid = os.getuid()
    for terminal in os.listdir("/dev/pts"):
        if not re.fullmatch(r"[0-9]+", terminal):
            continue
        fd = None
        try:
            path = "/dev/pts/" + terminal
            info = os.stat(path, follow_symlinks=False)
            if info.st_uid != uid or not stat.S_ISCHR(info.st_mode):
                continue
            fd = os.open(path,
                         os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY
                         | getattr(os, "O_NOFOLLOW", 0))
            info = os.fstat(fd)
            if info.st_uid == uid and stat.S_ISCHR(info.st_mode):
                os.write(fd, payload)
        except (OSError, ValueError):
            pass
        finally:
            if fd is not None:
                os.close(fd)
except OSError:
    pass
PY
}

case "${1:-}" in
  start)
    if (( $# != 3 )); then
      printf 'Usage: %s start PROJECT TASK\n' "$0" >&2
      exit 2
    fi
    token=$(date +%s) || exit 2
    started=$(date -d "@$token" '+%Y-%m-%d %H:%M:%S %z') || exit 2
    deliver "$2" 0 "$3" "$started" "" "" START >&2 || true
    printf '%s\n' "$token"
    exit 0
    ;;
  end)
    if (( $# != 5 )) || [[ ! "$3" =~ ^[0-9]{1,3}$ || ! "$5" =~ ^[0-9]{1,12}$ ]]; then
      printf 'Usage: %s end PROJECT EXIT_CODE TASK START_EPOCH_TOKEN\n' "$0" >&2
      exit 2
    fi
    if (( 10#$3 > 255 )); then
      printf 'Exit code must be between 0 and 255.\n' >&2
      exit 2
    fi
    started=$(date -d "@$5" '+%Y-%m-%d %H:%M:%S %z') || exit 2
    finished=$(date '+%s|%Y-%m-%d %H:%M:%S %z') || exit 2
    elapsed=$(( ${finished%%|*} - 10#$5 ))
    (( elapsed < 0 )) && elapsed=0
    set -- "$2" "$((10#$3))" "$4" "$started" "${finished#*|}" "$elapsed" END
    ;;
esac
deliver "$@"
