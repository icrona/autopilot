#!/usr/bin/env python3
"""Render <cockpit>/board.html (local only) from the registry, today's plan and health, the hook records and
`claude agents`. Also sends the reminder / window-at-risk notifications and keeps task states in sync.

  render_board.py            # render (hooks call this in the background)
  render_board.py --print    # render and print the terminal summary for /autopilot status"""
import argparse
import datetime as dt
import fcntl
import html
import subprocess
import time

import ap

LANES = [("needs-you", "Needs you"), ("working", "Working"), ("ready", "Ready for review"),
         ("planned", "Planned"), ("blocked", "Blocked"), ("done", "Done")]
LANE_TO_STATE = {"needs-you": "needs-you", "working": "dispatched", "ready": "ready-for-review",
                 "blocked": "blocked", "done": "done"}


def esc(value):
    return html.escape(str(value if value is not None else ""))


def shown(value):
    if value is None:
        return "n/a"
    return ", ".join(map(str, value)) if isinstance(value, list) else value


def minutes(hhmm):
    hours, mins = map(int, hhmm.split(":"))
    return hours * 60 + mins


def lane_for(task, record, row):
    status = ((record or {}).get("ap_status") or {}).get("status")
    state = (row or {}).get("state")
    if ap.done_but_holding_slot(record, row):
        return "needs-you"
    if task.get("state") == "done" or status == "done":
        return "done"
    if not (task.get("session") or {}).get("name") and not record:
        return {"blocked": "blocked", "needs-you": "needs-you"}.get(task.get("state"), "planned")
    if status == "needs-login" or (record or {}).get("pending"):
        return "needs-you"
    if status in {"ready-for-review"} or state == "done":
        return "ready"
    if state == "blocked":
        return "needs-you"
    if state == "working":
        return "working"
    if status in {"plan-ready", "needs-you"}:
        return "needs-you"
    if status in {"blocked", "failed"} or state in {"failed", "stopped"}:
        return "blocked"
    return "working"


def waiting_on(record, row):
    if ap.done_but_holding_slot(record, row):
        detail = (record["ap_status"].get("detail") or "").strip()
        return f"says done, still holding a BG slot: stop it with `claude stop {row.get('id')}`" + (f" · {detail}" if detail else "")
    record = record or {}
    if record.get("pending"):
        return f"{record['pending']['tool']}: {record['pending']['what']}"
    status = record.get("ap_status") or {}
    if status.get("status") in {"needs-login", "needs-you", "plan-ready", "blocked", "failed", "ready-for-review", "done"}:
        return f"{status['status']} {status.get('detail', '')}".strip()
    if (row or {}).get("state") == "blocked":
        return (record.get("last_notification") or {}).get("message") or "waiting for you"
    return ""


def since_minutes(record):
    stamp = ((record or {}).get("pending") or {}).get("since") or ((record or {}).get("ap_status") or {}).get("at")
    return round((time.time() - stamp) / 60) if stamp else None


def awake_guard():
    return subprocess.run(["pgrep", "-f", "caffeinate -ims"], capture_output=True).returncode == 0


def pr_watch_line(data):
    """(text, warn) for the PR watch (conflicts + feedback), from state/pr-watch.json."""
    watch = data["pr_watch"]
    checked = watch.get("checked_at")
    if watch.get("error_at") and (not checked or watch["error_at"] > checked):
        return f"PR watch error {watch['error_at'][11:16]}: {watch.get('error', '')[:120]}", True
    interval = watch.get("interval_s") or data["cfg"]["pr_watch"]["interval_s"]
    if ap.in_working_hours() and (not checked or (ap.now() - dt.datetime.fromisoformat(checked)).total_seconds() > 2 * interval):
        return f"PR watch not running ({f'last {checked[11:16]}' if checked else 'never ran'})", True
    if not checked:
        return "PR watch never ran", False
    conflicting = sum(1 for row in (watch.get("prs") or {}).values() if row.get("mergeable") == "CONFLICTING")
    text, warn = f"PR watch {checked[11:16]} · {conflicting} conflicting", False
    feedback = watch.get("feedback") or {}
    if feedback.get("checked_at"):
        text += f" · {sum(1 for row in (feedback.get('prs') or {}).values() if row.get('open'))} with feedback"
    if feedback.get("error_at") and (not feedback.get("checked_at") or feedback["error_at"] > feedback["checked_at"]):
        text, warn = text + f" · feedback error {feedback['error_at'][11:16]}: {feedback.get('error', '')[:80]}", True
    return text, warn


def collect():
    cfg, date = ap.config(), ap.today()
    tasks = ap.load_tasks()
    rows = ap.agents(include_done=True)
    latest = {}
    for row in sorted(rows, key=lambda row: row.get("startedAt", 0)):
        if row.get("name"):
            latest[row["name"]] = row
    slot_names = [row["name"] for row in ap.active_autopilot_sessions(rows)]
    cards = []
    known_names = set()
    health_name = f"ap-health-{date}"
    extra = [{"id": f"health-{date}", "title": "Morning health check", "type": "ops",
              "session": {"name": health_name}, "state": "dispatched"}] if (ap.SESSIONS / f"{health_name}.json").exists() else []
    for task in tasks + extra:
        if task.get("state") in {"inbox", "dropped", "deferred"}:
            continue
        if task.get("state") == "done" and not str(task.get("updated", "")).startswith(date):
            continue
        name = (task.get("session") or {}).get("name")
        known_names.add(name)
        record = ap.read_json(ap.SESSIONS / f"{name}.json") if name else None
        row = latest.get(name) if name else None
        cards.append({"task": task, "name": name, "record": record, "row": row,
                      "attach": ap.attach_command(task.get("session")), "slot": name in slot_names,
                      "lane": lane_for(task, record, row), "waiting": waiting_on(record, row),
                      "waited_min": since_minutes(record)})
    others = [row for row in rows if row.get("name") not in known_names and row.get("kind") == "interactive"]
    return {"cfg": cfg, "date": date, "tasks": tasks, "cards": cards, "others": others, "slots": slot_names,
            "plan": ap.read_json(ap.day_dir(date) / "plan.json"),
            "health": ap.read_json(ap.day_dir(date) / "health.json"),
            "logins": ap.read_json(ap.day_dir(date) / "logins.json", {}) or {},
            "inbox": [task for task in tasks if task.get("state") == "inbox"],
            "pr_watch": ap.read_json(ap.STATE / "pr-watch.json", {}) or {},
            "awake": awake_guard()}


def sync_and_sweep(data):
    cfg, changed = data["cfg"], False
    for card in data["cards"]:
        task, lane = card["task"], card["lane"]
        target = LANE_TO_STATE.get(lane)
        if card["name"] and target and task.get("state") not in {target, "done"} and task.get("id") in {
                stored["id"] for stored in data["tasks"]}:
            task["state"] = target
            task.setdefault("history", []).append({"state": target, "at": ap.now().isoformat(timespec="seconds")})
            changed = True
        if lane == "needs-you" and (card["waited_min"] or 0) >= cfg["notify"]["remind_after_min"]:
            ap.notify(f"{card['name']} has waited {card['waited_min']} min: {card['waiting'][:120]}",
                      key=f"remind:{card['name']}:{(card['record'] or {}).get('updated_at')}")
        if (card["row"] or {}).get("state") == "failed":
            ap.notify(f"{card['name']} failed", kind="urgent", key=f"failed-agent:{card['name']}")
        window = task.get("window") or {}
        if window.get("before") and lane not in {"done", "ready"}:
            deadline = next((ap.to_local(item["utc"]) for item in cfg["windows"] if item["id"] == window["before"]),
                            None) or ap.local_at(window["before"])
            left = (deadline - ap.now()).total_seconds() / 60
            if 0 < left <= cfg["notify"]["window_risk_min"]:
                ap.notify(f"{task['id']} must finish before {deadline:%H:%M} ({round(left)} min left) and is {lane}",
                          kind="urgent", key=f"window:{data['date']}:{task['id']}")
    if changed:
        ap.save_tasks(data["tasks"])


def timeline_html(data):
    plan = data["plan"]
    if not plan:
        return '<p class="muted">No plan yet. Run <code>/autopilot morning</code>.</p>'
    workday = data["cfg"]["workday"]
    start, end = minutes(workday["start"]), minutes(workday["end"])
    span = end - start

    def pos(hhmm):
        return max(0.0, min(100.0, (minutes(hhmm) - start) / span * 100))

    you, background = [], []
    for block in plan["blocks"]:
        if block["kind"] == "window":
            continue
        left, right = pos(block["start"]), pos(block.get("end", block["start"]))
        item = (f'<div class="blk k-{esc(block["kind"])}" style="left:{left:.2f}%;width:{max(right - left, 0.6):.2f}%" '
                f'title="{esc(block["start"])}–{esc(block.get("end", ""))} {esc(block.get("title") or block.get("task"))}">'
                f'<span>{esc(block.get("title") or block.get("task"))}</span></div>')
        (background if block["kind"] == "bg" else you).append((left, right, item))
    tracks = []
    for left, right, item in sorted(background):
        for track in tracks:
            if track[-1][1] <= left:
                track.append((left, right, item))
                break
        else:
            tracks.append([(left, right, item)])
    hours = "".join(f'<div class="hour" style="left:{(hour * 60 - start) / span * 100:.2f}%">{hour:02d}</div>'
                    for hour in range(start // 60, end // 60 + 1))
    windows = "".join(
        f'<div class="window" style="left:{pos(ap.to_local(window["utc"]).strftime("%H:%M")):.2f}%" '
        f'title="{esc(window["label"])}"><span>{esc(window["id"])} {ap.to_local(window["utc"]):%H:%M}</span></div>'
        for window in data["cfg"]["windows"])
    now_local = ap.now().strftime("%H:%M")
    now_line = f'<div class="now" style="left:{pos(now_local):.2f}%"></div>' if start <= minutes(now_local) <= end else ""
    rows = [f'<div class="track"><div class="label">You</div><div class="lane-area">{"".join(i for *_, i in you)}</div></div>']
    rows += [f'<div class="track"><div class="label">BG {index + 1}</div><div class="lane-area">{"".join(i for *_, i in track)}</div></div>'
             for index, track in enumerate(tracks)]
    return (f'<div class="timeline-wrap"><div class="timeline"><div class="hours">{hours}</div>{"".join(rows)}'
            f'<div class="overlay">{windows}{now_line}</div></div></div>')


def health_html(data):
    health = data["health"]
    if not health:
        return '<p class="muted">No health check today (optional module, see BLUEPRINT.md).</p>'
    tiles = []
    for result in health["results"]:
        signals = "".join(f'<li class="sig-{esc(signal["level"])}">{esc(signal["metric"])}: {esc(shown(signal["now"]))} '
                          f'<span class="muted">(base {esc(shown(signal["base"]))})</span></li>' for signal in result["signals"][:4])
        tiles.append(
            f'<div class="tile st-{esc(result["status"].lower())}"><div class="tile-head"><b>{esc(result["service"])}</b> '
            f'{esc(result["env"].upper())}<span class="pill">{esc(result["status"])}</span></div>'
            f'<div class="nums">{esc(result.get("summary", ""))}</div><ul>{signals}</ul></div>')
    return f'<div class="tiles">{"".join(tiles)}</div>'


def logins_html(data):
    needed = [f'{esc(system)} <span class="muted">(preflight)</span>' for system, state in
              (data["logins"].get("systems") or {}).items() if state != "ok"]
    needed += [f'{esc(card["record"]["ap_status"].get("detail"))} <span class="muted">({esc(card["name"])})</span>'
               for card in data["cards"] if ((card["record"] or {}).get("ap_status") or {}).get("status") == "needs-login"]
    if not needed:
        checked = data["logins"].get("checked_at")
        return f'<p class="muted">No login needed{f" · checked {esc(checked)}" if checked else ""}.</p>'
    return '<ul class="logins">' + "".join(f"<li>Log in: {item}</li>" for item in needed) + "</ul>"


def card_html(card):
    task, name, command = card["task"], card["name"], card["attach"]
    links = "".join(f'<a href="{esc(link.get("url") if isinstance(link, dict) else link)}">'
                    f'{esc(link.get("label") if isinstance(link, dict) else "link")}</a>' for link in task.get("links") or [])
    attach = (f'<div class="attach"><code>{esc(command)}</code><span class="muted">{esc(name)}</span>'
              f'<button onclick="copy(this)" data-cmd="{esc(command)}">copy</button></div>') if command else ""
    waited = f' · {card["waited_min"]} min' if card["lane"] == "needs-you" and card["waited_min"] else ""
    waiting = f'<div class="waiting">{esc(card["waiting"])}{waited}</div>' if card["waiting"] else ""
    flags = task.get("flags") or {}
    badges = ('<span class="badge">bg slot</span>' if card["slot"] else "") + "".join(
        f'<span class="badge lock">{label} lock</span>' for flag, label in (("chrome", "chrome"), ("local_stack", "local stack"))
        if card["slot"] and flags.get(flag))
    return (f'<div class="card{" slot" if card["slot"] else ""}"><div class="card-head"><b>{esc(task["id"])}</b>{badges}'
            f'<span class="tag">{esc(task.get("type", ""))}</span>'
            f'<span class="tag">{esc(task.get("repo") or "")}</span></div><div class="title">{esc(task.get("title", ""))}</div>'
            f'{waiting}{attach}<div class="links">{links}</div></div>')


def board_html(data):
    lanes = []
    for lane, label in LANES:
        cards = [card_html(card) for card in data["cards"] if card["lane"] == lane]
        lanes.append(f'<section class="lane lane-{lane}"><h3>{label} <span class="count">{len(cards)}</span></h3>'
                     f'{"".join(cards) or "<p class=muted>—</p>"}</section>')
    others = "".join(f'<li>{esc(row.get("name"))} <span class="muted">{esc(row.get("status") or row.get("state"))} · '
                     f'{esc(str(row.get("cwd", "")).replace(str(ap.expand("~")), "~"))}</span></li>' for row in data["others"])
    inbox = "".join(f'<li>{esc(task["id"])} <span class="muted">{esc(task.get("title", ""))}</span></li>' for task in data["inbox"])
    awake = '<span class="pill ok">awake guard on</span>' if data["awake"] else '<span class="pill warn">awake guard off</span>'
    watch_text, watch_warn = pr_watch_line(data)
    watch = f'<span class="pill warn">{esc(watch_text)}</span>' if watch_warn else f' · {esc(watch_text)}'
    slots_text, slots_full = slots_line(data)
    slots = f'<span class="pill{" warn" if slots_full else ""}">{esc(slots_text)}</span>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="20">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Autopilot board</title><style>
:root{{--bg:#f6f7f9;--panel:#fff;--ink:#1d2330;--muted:#6b7385;--line:#e2e5eb;--accent:#3557d6;--ok:#1f8a4c;--warn:#b7791f;
--bad:#c53030;--you:#3557d6;--bgrun:#7c5cc4;--meet:#8a94a6;--soft:#eef1f6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12151b;--panel:#1b1f27;--ink:#e6e9ef;--muted:#98a1b3;--line:#2c323d;
--accent:#7b97ff;--ok:#48bb78;--warn:#ecc94b;--bad:#fc8181;--you:#6f8cff;--bgrun:#a58bf0;--meet:#5d6678;--soft:#232834}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1400px;margin:0 auto;padding:16px}}header{{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;justify-content:space-between}}
h1{{font-size:20px;margin:0}}h2{{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:22px 0 8px}}
h3{{font-size:14px;margin:0 0 8px}}.muted{{color:var(--muted)}}code{{font:12px ui-monospace,Menlo,monospace}}
.pill{{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;margin-left:8px;background:var(--soft)}}
.pill.ok,.st-ok .pill{{color:var(--ok)}}.pill.warn,.st-watch .pill{{color:var(--warn)}}.st-anomaly .pill{{color:#fff;background:var(--bad)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}}
.tile{{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--ok);border-radius:8px;padding:10px 12px}}
.tile.st-watch{{border-left-color:var(--warn)}}.tile.st-anomaly{{border-left-color:var(--bad)}}.tile ul{{margin:6px 0 0;padding-left:18px}}
.nums{{font-size:12px;color:var(--muted);margin-top:4px}}.sig-anomaly{{color:var(--bad)}}.sig-watch{{color:var(--warn)}}
.logins{{background:var(--panel);border:1px solid var(--bad);border-radius:8px;padding:8px 12px 8px 28px;margin:0}}
.timeline-wrap{{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px}}
.timeline{{position:relative;min-width:760px;padding-bottom:22px}}.hours{{position:relative;height:18px;margin-left:56px}}
.hour{{position:absolute;font-size:11px;color:var(--muted);transform:translateX(-50%)}}
.track{{display:flex;align-items:center;height:30px}}.label{{width:56px;font-size:11px;color:var(--muted)}}
.lane-area{{position:relative;flex:1;height:24px;background:var(--soft);border-radius:4px}}
.blk{{position:absolute;top:2px;bottom:2px;border-radius:4px;background:var(--you);color:#fff;font-size:11px;overflow:hidden;
white-space:nowrap;padding:3px 5px}}.k-bg{{background:var(--bgrun)}}.k-meeting,.k-lunch{{background:var(--meet)}}
.k-touchpoint,.k-login-check{{background:var(--warn);color:#1d2330}}.overlay{{position:absolute;top:18px;bottom:22px;left:56px;right:0;pointer-events:none}}
.window{{position:absolute;top:0;bottom:0;border-left:2px dashed var(--bad)}}.window span{{position:absolute;bottom:-16px;font-size:10px;color:var(--bad);white-space:nowrap}}
.now{{position:absolute;top:0;bottom:0;border-left:2px solid var(--accent)}}
.board{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}}
.lane{{background:var(--soft);border-radius:8px;padding:10px}}.lane-needs-you{{outline:2px solid var(--bad)}}
.count{{color:var(--muted);font-weight:400}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px;margin-bottom:8px}}
.card-head{{display:flex;gap:6px;align-items:center;flex-wrap:wrap}}.tag{{font-size:11px;color:var(--muted)}}.title{{margin:4px 0}}
.waiting{{font-size:12px;color:var(--muted);margin:4px 0;word-break:break-word}}.lane-needs-you .waiting,.lane-blocked .waiting{{color:var(--bad)}}.attach{{display:flex;gap:6px;align-items:center}}
.attach button{{font-size:11px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:4px;cursor:pointer}}
.card.slot{{border:2px solid var(--bgrun);box-shadow:0 0 0 1px var(--bgrun)}}
.badge{{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:#fff;background:var(--bgrun);border-radius:99px;padding:0 6px}}
.badge.lock{{background:var(--soft);color:var(--bgrun);border:1px solid var(--bgrun)}}
.links a{{font-size:12px;margin-right:8px;color:var(--accent)}}ul.plain{{margin:0;padding-left:18px}}
</style></head><body><main><header><h1>Autopilot · {esc(data["date"])}</h1>
<div class="muted">updated {ap.now():%H:%M:%S} {esc(ap.tz_label())}{watch} {slots} {awake}</div></header>
<h2>Health</h2>{health_html(data)}<h2>Logins</h2>{logins_html(data)}
<h2>Day plan</h2>{timeline_html(data)}<h2>Tasks</h2><div class="board">{"".join(lanes)}</div>
<h2>Inbox (not planned)</h2><ul class="plain">{inbox or "<li class=muted>empty</li>"}</ul>
<h2>Other sessions</h2><ul class="plain">{others or "<li class=muted>none</li>"}</ul></main>
<script>function copy(button){{const text=button.dataset.cmd;(navigator.clipboard?navigator.clipboard.writeText(text):Promise.reject())
.catch(()=>{{const area=document.createElement('textarea');area.value=text;document.body.appendChild(area);area.select();
document.execCommand('copy');area.remove();}}).finally(()=>{{button.textContent='copied';}});}}</script></body></html>"""


def slots_line(data):
    """Background slots in use (the sessions dispatch.py counts) and whether they are all taken."""
    limit, names = data["cfg"]["max_parallel_background"], data["slots"]
    return f"BG slots {len(names)}/{limit}" + (f": {', '.join(names)}" if names else ""), len(names) >= limit


def print_summary(data):
    by_lane = {lane: [card for card in data["cards"] if card["lane"] == lane] for lane, _ in LANES}
    print(f"Autopilot {data['date']} · {ap.now():%H:%M} {ap.tz_label()} · awake guard {'on' if data['awake'] else 'OFF'}")
    if data["health"]:
        print("Health: " + " · ".join(f"{result['service']} {result['env'].upper()} {result['status']}"
                                      for result in data["health"]["results"]))
    print(pr_watch_line(data)[0])
    print(slots_line(data)[0])
    logins = [system for system, state in (data["logins"].get("systems") or {}).items() if state != "ok"]
    if logins:
        print("Log in: " + ", ".join(logins))
    for lane, label in LANES:
        if lane == "done" or not by_lane[lane]:
            continue
        print(f"\n{label}:")
        for card in by_lane[lane]:
            line = f"  {card['task']['id']:<20} {card['task'].get('title', '')[:60]}"
            if card["waiting"]:
                line += f"\n      → {card['waiting'][:140]}"
            if card["attach"] and lane in {"needs-you", "ready", "working"}:
                line += f"\n      {card['attach']}  ({card['name']})"
            print(line)
    print(f"\nDone today: {len(by_lane['done'])} · inbox: {len(data['inbox'])} · other sessions: {len(data['others'])}")
    print(f"Board: file://{ap.ROOT / 'board.html'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args()
    with open(ap.STATE / ".render.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = collect()
        sync_and_sweep(data)
        (ap.ROOT / "board.html").write_text(board_html(data))
    if args.print:
        print_summary(data)


if __name__ == "__main__":
    main()
