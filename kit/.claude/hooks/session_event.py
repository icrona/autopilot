#!/usr/bin/env python3
"""Lifecycle hook for autopilot sessions (SessionStart, PermissionRequest, Notification, Stop, SessionEnd, plus
PreToolUse, PostToolUse and UserPromptSubmit, which only clear a permission prompt the session has moved past).
Records what each session is doing in state/sessions/<name>.json, raises urgent desktop notifications,
and re-renders the board. Never decides a permission: it only observes."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "autopilot" / "scripts"))
import ap  # noqa: E402

STATUS_LINE = re.compile(r"^\s*AP-STATUS:\s*(?P<status>[\w-]+)\s*(?P<detail>.*)$", re.MULTILINE)
# Events that prove a session is past its permission prompt. PreToolUse also catches a denial (the next tool starts).
MOVED_ON = {"PreToolUse", "PostToolUse", "UserPromptSubmit"}


def resolve_name(payload):
    name = os.environ.get("AUTOPILOT_TASK")
    if name:
        return name
    index = ap.read_json(ap.SESSIONS / "index.json", {})
    session_id = payload.get("session_id", "")
    if session_id in index:
        return index[session_id]
    try:
        rows = json.loads(subprocess.run(["claude", "agents", "--json", "--all"], capture_output=True,
                                         text=True, timeout=15).stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        rows = []
    name = next((row.get("name") for row in rows if row.get("sessionId") == session_id), None)
    if name and name.startswith("ap-"):
        index[session_id] = name
        ap.write_json(ap.SESSIONS / "index.json", index)
        return name
    return None


def summarise_tool(tool_name, tool_input):
    if tool_name == "Bash":
        return tool_input.get("command", "")[:300]
    if tool_name == "ExitPlanMode":
        return "plan ready for your approval"
    for key in ("text", "message", "body", "summary", "title", "file_path"):
        if tool_input.get(key):
            return f"{key}: {str(tool_input[key])[:200]}"
    return json.dumps(tool_input)[:200]


def main():
    payload = json.load(sys.stdin)
    name = resolve_name(payload)
    if not name:
        return
    event = payload.get("hook_event_name", "")
    path = ap.SESSIONS / f"{name}.json"
    record = ap.read_json(path, {"name": name, "events": 0})
    if event in MOVED_ON and "pending" not in record:
        return  # fires on every tool call; only worth a write when a stale prompt needs clearing
    stamp = int(time.time())
    record.update(session_id=payload.get("session_id"), cwd=payload.get("cwd"),
                  permission_mode=payload.get("permission_mode"), last_event=event, updated_at=stamp,
                  events=record.get("events", 0) + 1)

    if event == "SessionStart":
        record.setdefault("started_at", stamp)
        record.pop("ended", None)
    elif event == "PermissionRequest":
        tool_name = payload.get("tool_name", "")
        record["pending"] = {"tool": tool_name, "what": summarise_tool(tool_name, payload.get("tool_input") or {}),
                             "since": stamp}
    elif event in MOVED_ON:
        record.pop("pending", None)  # the prompt was answered (or denied) and the session is running again
    elif event == "Notification":
        record["last_notification"] = {"type": payload.get("notification_type"),
                                       "message": (payload.get("message") or "")[:200], "at": stamp}
    elif event == "Stop":
        message = payload.get("last_assistant_message") or ""
        record["last_message"] = message[-600:]
        record.pop("pending", None)
        statuses = STATUS_LINE.findall(message)
        if statuses:
            status, detail = statuses[-1]
            record["ap_status"] = {"status": status.lower(), "detail": detail.strip(), "at": stamp}
            if status.lower() == "needs-login":
                ap.notify(f"Login needed: {detail.strip()} ({name})", kind="urgent",
                          key=f"login:{name}:{detail.strip()}")
            elif status.lower() == "failed":
                ap.notify(f"{name} failed: {detail.strip()[:120]}", kind="urgent", key=f"failed:{name}:{stamp}")
    elif event == "SessionEnd":
        record["ended"] = {"reason": payload.get("reason"), "at": stamp}
        record.pop("pending", None)
        task = next((task for task in ap.load_tasks() if ap.session_name(task["id"]) == name), {})
        finished = ((record.get("ap_status") or {}).get("status") in {"done", "ready-for-review"}
                    or task.get("state") in {"done", "dropped", "deferred"})
        if not finished and payload.get("reason") not in {"clear", "logout", "prompt_input_exit"}:
            ap.notify(f"{name} stopped before finishing ({payload.get('reason')})", kind="urgent",
                      key=f"ended:{name}:{stamp}")

    ap.write_json(path, record)
    ap.render_board_async()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # an observer hook must never break the session
        print(f"autopilot session_event: {error}", file=sys.stderr)
