"""Shared helpers for the autopilot cockpit: paths, config, task registry, sessions, notifications."""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("AUTOPILOT_HOME", Path.home() / "autopilot")).expanduser()
STATE = ROOT / "state"
SESSIONS = STATE / "sessions"
TASKS_FILE = STATE / "tasks.json"
NOTIFIED_FILE = STATE / "notified.json"
NOTIFY_LOG = STATE / "notifications.log"
SCRIPTS = Path(__file__).resolve().parent

ACTIVE_AGENT_STATES = {"working", "blocked"}


def config():
    return json.loads((ROOT / "config.json").read_text())


def owner():
    """The person the cockpit works for, as named in briefs, prompts and notifications."""
    return config()["owner"]["name"]


def tz_label():
    return config().get("timezone_label") or now().strftime("%Z")


def tz():
    return ZoneInfo(config()["timezone"])


def now():
    return dt.datetime.now(tz())


def today():
    return now().date().isoformat()


def day_dir(date=None):
    path = STATE / "days" / (date or today())
    path.mkdir(parents=True, exist_ok=True)
    return path


def expand(path):
    return Path(os.path.expanduser(path))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def load_tasks():
    return read_json(TASKS_FILE, {"tasks": []})["tasks"]


def save_tasks(tasks):
    write_json(TASKS_FILE, {"tasks": tasks})


def find_task(tasks, task_id):
    return next((task for task in tasks if task["id"] == task_id), None)


def session_name(task_id):
    return task_id if task_id.startswith("ap-") else f"ap-{task_id}"


def attach_command(session):
    """`claude attach <id>`: the id opens a session whether it's running or finished, a name only a running one."""
    session = session or {}
    name = session.get("name")
    background_id = session.get("id")
    if not background_id and name:
        background_id = (read_json(SESSIONS / f"{name}.json", {}) or {}).get("background_id")
    target = background_id or name
    return f"claude attach {target}" if target else None


def agents(include_done=True):
    """Sessions known to `claude agents` (background and interactive); [] if the CLI is unavailable."""
    command = ["claude", "agents", "--json"] + (["--all"] if include_done else [])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return json.loads(result.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def active_autopilot_sessions(agent_rows=None):
    rows = agent_rows if agent_rows is not None else agents(include_done=False)
    return [row for row in rows
            if (row.get("name") or "").startswith("ap-") and row.get("kind") == "background"
            and row.get("state") in ACTIVE_AGENT_STATES]


def done_but_holding_slot(record, row):
    """The session reported AP-STATUS done but its process is still alive, so it still counts against
    max_parallel_background. It stays alive until it's stopped."""
    status = ((record or {}).get("ap_status") or {}).get("status")
    return (status == "done" and (row or {}).get("kind") == "background"
            and (row or {}).get("state") in ACTIVE_AGENT_STATES)


def to_local(utc_hhmm, date=None):
    """'05:00' UTC on the given local date → aware local datetime."""
    day = dt.date.fromisoformat(date or today())
    hours, minutes = map(int, utc_hhmm.split(":"))
    return dt.datetime(day.year, day.month, day.day, hours, minutes, tzinfo=dt.timezone.utc).astimezone(tz())


def local_at(hhmm, date=None):
    day = dt.date.fromisoformat(date or today())
    hours, minutes = map(int, hhmm.split(":"))
    return dt.datetime(day.year, day.month, day.day, hours, minutes, tzinfo=tz())


def in_working_hours(moment=None):
    moment = moment or now()
    workday = config()["workday"]
    return local_at(workday["start"], moment.date().isoformat()) <= moment < local_at(workday["end"], moment.date().isoformat())


def notify(message, kind="normal", key=None, title="Autopilot"):
    """Desktop notification (macOS by default, or a notifier adapter). Outside working hours only kinds in notify.quiet_hours_allow get through.
    `key` dedupes: the same key never notifies twice."""
    if key:
        sent = read_json(NOTIFIED_FILE, {})
        if key in sent:
            return False
    if not in_working_hours() and kind not in config()["notify"]["quiet_hours_allow"]:
        return False
    channel = (config().get("notify") or {}).get("channel") or "macos"
    if channel == "macos":
        text = message.replace("\\", "\\\\").replace('"', '\\"')[:200]
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}" sound name "Glass"'],
                       capture_output=True, timeout=10)
    else:  # notifier adapter: adapters/notifier/<channel> <title> <message> <kind> (see adapters/README.md)
        subprocess.run([str(SCRIPTS.parent / "adapters" / "notifier" / channel), title, message[:500], kind],
                       capture_output=True, timeout=30)
    with NOTIFY_LOG.open("a") as log:
        log.write(f"{now().isoformat(timespec='seconds')}\t{kind}\t{message}\n")
    if key:
        sent = read_json(NOTIFIED_FILE, {})
        sent[key] = int(time.time())
        write_json(NOTIFIED_FILE, sent)
    return True


def render_board_async():
    """Re-render board.html without blocking the caller (used from hooks)."""
    subprocess.Popen([sys.executable, str(SCRIPTS / "render_board.py")], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, cwd=str(ROOT))
