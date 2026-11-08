#!/bin/bash
# Started by the LaunchAgent (install.sh). Keeps one older log, then runs the companion server in the foreground
# so launchd owns the process (stop = SIGTERM from the menu bar app).
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG="$ROOT/logs/companion_server.log"
mkdir -p "$ROOT/logs"
if [ -f "$LOG" ] && [ "$(stat -f %z "$LOG")" -gt 10485760 ]; then
  mv -f "$LOG" "$LOG.1"
fi
cd "$ROOT" || exit 1
export VIRTUAL_ENV="$ROOT/.venv" PYTHONUNBUFFERED=1
echo "---- $(date '+%Y-%m-%d %H:%M:%S') start" >> "$LOG"
exec "$ROOT/.venv/bin/python" companion_server.py >> "$LOG" 2>&1
