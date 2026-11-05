#!/usr/bin/env python3
"""Task registry and day-plan helper.

  tasks.py list [--all] [--state planned,dispatched] [--json]
  tasks.py upsert < task.json            # one task object or a list; merged by id
  tasks.py set <id> key=value ...        # dotted keys allowed (session.name=ap-ENG-12); values parsed as JSON when possible
  tasks.py capacity [--date D]           # free slots from the workday, lunch and meetings in days/D/intake.json
  tasks.py validate-plan [--date D]      # checks days/D/plan.json against hours, overlaps, limits, locks, windows
  tasks.py plan-md [--date D]            # writes days/D/plan.md from plan.json
  tasks.py wrap [--date D]               # done / carry-over / estimate vs actual
"""
import argparse
import datetime as dt
import json
import sys

import ap

OPEN_STATES = {"inbox", "planned", "dispatched", "needs-you", "ready-for-review", "blocked"}
ATTENTION_KINDS = {"focus", "touchpoint", "meeting", "lunch", "login-check", "wrap", "triage"}


def parse_value(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def cmd_list(args):
    tasks = ap.load_tasks()
    states = set(args.state.split(",")) if args.state else (None if args.all else OPEN_STATES)
    rows = [task for task in tasks if states is None or task.get("state") in states]
    rows.sort(key=lambda task: (task.get("priority", 9), task.get("created", "")))
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for task in rows:
        session = (task.get("session") or {}).get("name", "")
        print(f"{task['id']:<22} {task.get('state', ''):<17} P{task.get('priority', '?')} {task.get('type', ''):<9} "
              f"{task.get('effort_min', '?'):>4}m {task.get('repo') or '-':<17} {session:<22} {task.get('title', '')[:70]}")


def cmd_upsert(_args):
    incoming = json.load(sys.stdin)
    incoming = incoming if isinstance(incoming, list) else [incoming]
    tasks = ap.load_tasks()
    stamp = ap.now().isoformat(timespec="seconds")
    for item in incoming:
        existing = ap.find_task(tasks, item["id"])
        if existing:
            existing.update(item)
            existing["updated"] = stamp
        else:
            tasks.append({"state": "inbox", "created": stamp, "updated": stamp, **item})
    ap.save_tasks(tasks)
    print(f"upserted {len(incoming)} task(s); registry has {len(tasks)}")


def cmd_set(args):
    tasks = ap.load_tasks()
    task = ap.find_task(tasks, args.id)
    if not task:
        sys.exit(f"no task {args.id}")
    for pair in args.pairs:
        key, _, raw = pair.partition("=")
        target = task
        *parents, leaf = key.split(".")
        for parent in parents:
            target = target.setdefault(parent, {})
        target[leaf] = parse_value(raw)
    if "state" in [pair.split("=")[0] for pair in args.pairs]:
        task.setdefault("history", []).append({"state": task["state"], "at": ap.now().isoformat(timespec="seconds")})
    task["updated"] = ap.now().isoformat(timespec="seconds")
    ap.save_tasks(tasks)
    print(json.dumps(task, indent=2, ensure_ascii=False))


def minutes(hhmm):
    hours, mins = map(int, hhmm.split(":"))
    return hours * 60 + mins


def hhmm(total):
    return f"{total // 60:02d}:{total % 60:02d}"


def window_minutes(ref, date):
    """A task window refers to a config window id ('release-cutoff') or a local 'HH:MM'."""
    for window in ap.config()["windows"]:
        if window["id"] == ref:
            return minutes(ap.to_local(window["utc"], date).strftime("%H:%M"))
    return minutes(ref)


def free_slots(date):
    workday = ap.config()["workday"]
    lunch = workday.get("lunch")  # null or absent: no lunch break (e.g. an evening-only workday)
    busy = [(minutes(lunch["start"]), minutes(lunch["end"]))] if lunch else []
    intake = ap.read_json(ap.day_dir(date) / "intake.json", {}) or {}
    busy += [(minutes(meeting["start"]), minutes(meeting["end"])) for meeting in intake.get("meetings", [])]
    busy.sort()
    cursor, end, slots = minutes(workday["start"]), minutes(workday["end"]), []
    for start, stop in busy:
        if start > cursor:
            slots.append((cursor, min(start, end)))
        cursor = max(cursor, stop)
    if cursor < end:
        slots.append((cursor, end))
    return [(start, stop) for start, stop in slots if stop > start]


def cmd_capacity(args):
    date = args.date or ap.today()
    slots = free_slots(date)
    free = sum(stop - start for start, stop in slots)
    buffer = round(free * ap.config()["workday"]["interrupt_buffer_pct"] / 100)
    print(f"Free slots {date} ({ap.config()['timezone']}):")
    for start, stop in slots:
        print(f"  {hhmm(start)}–{hhmm(stop)}  ({stop - start} min)")
    print(f"Free {free} min, interrupt buffer {buffer} min → plannable attention {free - buffer} min")
    for window in ap.config()["windows"]:
        print(f"  window {window['id']:<11} {ap.to_local(window['utc'], date).strftime('%H:%M')}  {window['label']}")


def overlaps(blocks):
    ordered = sorted(blocks, key=lambda block: block[0])
    return [(first, second) for first, second in zip(ordered, ordered[1:]) if second[0] < first[1]]


def cmd_validate(args):
    date = args.date or ap.today()
    plan = ap.read_json(ap.day_dir(date) / "plan.json")
    if not plan:
        sys.exit(f"no plan.json for {date}")
    cfg, tasks = ap.config(), {task["id"]: task for task in ap.load_tasks()}
    workday = (minutes(cfg["workday"]["start"]), minutes(cfg["workday"]["end"]))
    problems, warnings = [], []
    blocks = []
    for block in plan["blocks"]:
        start, end = minutes(block["start"]), minutes(block.get("end", block["start"]))
        blocks.append((start, end, block))
        if block["kind"] != "meeting" and (start < workday[0] or end > workday[1]):
            problems.append(f"{block['start']}–{block.get('end')} {block.get('title', '')}: outside the workday")

    attention = [(start, end, block) for start, end, block in blocks if block["kind"] in ATTENTION_KINDS]
    for first, second in overlaps(attention):
        problems.append(f"your time overlaps: {first[2].get('title')} {hhmm(first[0])}–{hhmm(first[1])} and "
                        f"{second[2].get('title')} {hhmm(second[0])}–{hhmm(second[1])}")

    background = [(start, end, block) for start, end, block in blocks if block["kind"] == "bg"]
    for minute in range(workday[0], workday[1]):
        running = [block for start, end, block in background if start <= minute < end]
        if len(running) > cfg["max_parallel_background"]:
            problems.append(f"{hhmm(minute)}: {len(running)} background sessions (max {cfg['max_parallel_background']})")
            break
    for flag in ("local_stack", "chrome"):
        holders = [(start, end, block) for start, end, block in background
                   if (tasks.get(block.get("task")) or {}).get("flags", {}).get(flag)]
        for first, second in overlaps(holders):
            problems.append(f"{flag} lock: {first[2].get('task')} and {second[2].get('task')} overlap")

    for task_id, task in tasks.items():
        window = task.get("window") or {}
        mine = [(start, end) for start, end, block in blocks if block.get("task") == task_id and block["kind"] != "touchpoint"]
        if window.get("before") and mine and max(end for _, end in mine) > window_minutes(window["before"], date):
            problems.append(f"{task_id} must finish before {window['before']}")
        if window.get("after") and mine and min(start for start, _ in mine) < window_minutes(window["after"], date):
            problems.append(f"{task_id} must start after {window['after']}")

    planned_ids = {block.get("task") for block in plan["blocks"]} | set(plan.get("not_today", []))
    for task_id, task in tasks.items():
        if task.get("state") == "planned" and task_id not in planned_ids:
            warnings.append(f"{task_id} is 'planned' but has no block and is not in not_today")

    free = sum(stop - start for start, stop in free_slots(date))
    budget = free - round(free * cfg["workday"]["interrupt_buffer_pct"] / 100)
    used = sum(end - start for start, end, block in attention if block["kind"] not in {"meeting", "lunch"})
    if used > budget:
        warnings.append(f"attention planned {used} min > budget {budget} min (after the interrupt buffer)")

    for line in problems:
        print(f"PROBLEM  {line}")
    for line in warnings:
        print(f"WARNING  {line}")
    print(f"attention planned {used}/{budget} min · {len(background)} background runs · "
          f"{'OK' if not problems else f'{len(problems)} problem(s)'}")
    sys.exit(1 if problems else 0)


def cmd_plan_md(args):
    date = args.date or ap.today()
    plan = ap.read_json(ap.day_dir(date) / "plan.json")
    if not plan:
        sys.exit(f"no plan.json for {date}")
    tasks = {task["id"]: task for task in ap.load_tasks()}
    lines =[f"# Day plan {date} ({'approved' if plan.get('approved') else 'proposed'})", ""]
    for block in sorted(plan["blocks"], key=lambda block: minutes(block["start"])):
        task = tasks.get(block.get("task"), {})
        span = f"{block['start']}–{block['end']}" if block.get("end") else block["start"]
        lines.append(f"- **{span}** · {block['kind']} · {block.get('title') or task.get('title', '')}"
                     + (f" (`{block['task']}`)" if block.get("task") else ""))
    if plan.get("not_today"):
        lines += ["", "## Not today", *[f"- `{task_id}` {tasks.get(task_id, {}).get('title', '')}" for task_id in plan["not_today"]]]
    if plan.get("notes"):
        lines += ["", "## Notes", plan["notes"]]
    (ap.day_dir(date) / "plan.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def cmd_wrap(args):
    date = args.date or ap.today()
    tasks = ap.load_tasks()
    done = [task for task in tasks if task.get("state") == "done" and task.get("updated", "").startswith(date)]
    carry = [task for task in tasks if task.get("state") in OPEN_STATES]
    print(f"## Done {date}")
    for task in done:
        record = ap.read_json(ap.SESSIONS / f"{(task.get('session') or {}).get('name', '')}.json", {}) or {}
        actual = ""
        if record.get("started_at") and record.get("updated_at"):
            actual = f" · actual ~{round((record['updated_at'] - record['started_at']) / 60)} min (session span)"
        print(f"- {task['id']} {task.get('title', '')} · est {task.get('effort_min', '?')} min{actual}")
    print("\n## Carry over")
    for task in carry:
        command = ap.attach_command(task.get("session"))
        print(f"- {task['id']} [{task.get('state')}] {task.get('title', '')}" + (f" · `{command}`" if command else ""))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--all", action="store_true")
    listing.add_argument("--state")
    listing.add_argument("--json", action="store_true")
    sub.add_parser("upsert")
    setter = sub.add_parser("set")
    setter.add_argument("id")
    setter.add_argument("pairs", nargs="+")
    for name in ("capacity", "validate-plan", "plan-md", "wrap"):
        sub.add_parser(name).add_argument("--date")
    args = parser.parse_args()
    {"list": cmd_list, "upsert": cmd_upsert, "set": cmd_set, "capacity": cmd_capacity,
     "validate-plan": cmd_validate, "plan-md": cmd_plan_md, "wrap": cmd_wrap}[args.command](args)


if __name__ == "__main__":
    main()
