#!/bin/bash
# Keep the Mac awake for as long as the calling Claude Code session (the cockpit) lives:
# finds the nearest ancestor process named "claude" and runs `caffeinate -ims -w <that pid>`.
# Used when the cockpit was opened without bin/autopilot. Changes no system settings.
if pgrep -f "caffeinate -ims" >/dev/null; then
  echo "awake guard already on"
  exit 0
fi
pid=$$
while [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
  if [ "$(basename "$(ps -o comm= -p "$pid")")" = "claude" ]; then
    nohup caffeinate -ims -w "$pid" >/dev/null 2>&1 &
    echo "awake guard on: caffeinate -ims -w $pid (ends when that Claude session exits)"
    exit 0
  fi
  pid=$(ps -o ppid= -p "$pid" | tr -d ' ')
done
echo "no ancestor claude process found; start the cockpit with <cockpit>/bin/autopilot" >&2
exit 1
