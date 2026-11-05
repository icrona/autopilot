#!/usr/bin/env python3
"""Start one autopilot task as a background Claude Code session in its repo.

  dispatch.py <task-id>           # check only: limits, locks, brief, and the exact command (default)
  dispatch.py <task-id> --go      # launch (only after the owner's yes in the cockpit)
  dispatch.py health [--go]       # optional module: the morning health session (cwd = cockpit, --chrome, no plan
                                  # mode); needs references/health-brief.md

The brief must exist at days/<date>/briefs/<task-id>.md.
The standard autopilot protocol is appended to every brief."""
import argparse
import json
import os
import re
import subprocess
import sys

import ap

PROTOCOL = """

---
## Autopilot protocol (applies to this whole session)
- You are autopilot task `{name}`, dispatched from {owner}'s cockpit ({cockpit}). Work only on this task.
- {plan_rule}
- Never push, open/edit/comment/merge PRs, write to an issue tracker, wiki or chat, send mail, publish a release or
  artifact, dispatch a workflow or deploy without first showing the exact content or command and getting {owner}'s
  explicit yes for that one item. Plan approval is not consent to push. Never deploy or merge at all; {owner} does that.
{account_rule}- Logins (any web tool the task needs): if the page only needs an SSO button, click it. If it asks for a password, MFA
  or any other credential, stop. Never type, ask for or read credentials, and never switch to another auth
  path (tokens, other accounts, other endpoints). End the turn with `AP-STATUS: needs-login <system> [<env>]` and carry
  on only with work that needs no login. When the cockpit says the login is done, retry.
- End every turn with exactly one status line as the last line of your message, one of:
  `AP-STATUS: plan-ready` · `AP-STATUS: working <what>` · `AP-STATUS: needs-you <question>` ·
  `AP-STATUS: ready-for-review <one-line summary>` · `AP-STATUS: needs-login <system> [<env>]` ·
  `AP-STATUS: blocked <reason>` · `AP-STATUS: failed <reason>` · `AP-STATUS: done <one-line summary>`
- Final summary: what changed, the tests you ran with their counts, and what is waiting for {owner}'s approval.
"""

PLAN_RULE = ("You start in plan mode: investigate, then present a plan. After the owner approves it you may edit, run tests "
             "and commit locally in your worktree.")
NO_PLAN_RULE = "This task is read-only: do not edit any repository file; write only the outputs named in the brief."
ACCOUNT_RULE = ("- GitHub account: this repo belongs to `{account}`. `gh` and `git` act as whichever account is active, so "
                "check `gh auth status` before you propose a push or any gh write. If the active account is another one, "
                "or a call answers 403/404, never switch accounts or pass a token yourself: end the turn with "
                "`AP-STATUS: needs-you make {account} the active gh account`.\n")


def health_task(date):
    return {"id": f"health-{date}", "title": "Morning health check", "type": "ops", "repo": None,
            "flags": {"chrome": True, "plan_mode": False}, "state": "planned", "effort_min": 20}


def active_rows():
    rows = ap.agents(include_done=False)
    return rows, ap.active_autopilot_sessions(rows)


def trusted(path):
    """Background sessions refuse an untrusted folder; only the folder's own trust flag counted in testing."""
    projects = (ap.read_json(ap.expand("~/.claude.json"), {}) or {}).get("projects", {})
    candidates = {str(path), str(path).rstrip("/"), str(ap.expand(path).resolve())}
    return any((projects.get(key) or {}).get("hasTrustDialogAccepted") for key in candidates)


def checks(task, name, active, tasks, cwd):
    cfg, problems = ap.config(), []
    if not trusted(cwd):
        problems.append(f"{cwd} is not a trusted workspace: the owner runs `claude` there once and accepts the trust prompt")
    if any(row.get("name") == name for row in active):
        problems.append(f"{name} is already running: `claude attach {name}`")
    if len(active) >= cfg["max_parallel_background"]:
        problems.append(f"{len(active)} autopilot sessions already running (max {cfg['max_parallel_background']}): "
                        + ", ".join(row["name"] for row in active))
        finished = [row for row in active if ap.done_but_holding_slot(ap.read_json(ap.SESSIONS / f"{row['name']}.json"), row)]
        if finished:
            problems.append("these report done but still hold a slot; stop one to free it: "
                            + ", ".join(f"{row['name']} (`claude stop {row['id']}`)" for row in finished))
    by_name = {ap.session_name(other["id"]): other for other in tasks}
    for flag in ("chrome", "local_stack"):
        if (task.get("flags") or {}).get(flag):
            holders = [row["name"] for row in active if ((by_name.get(row["name"]) or {}).get("flags") or {}).get(flag)
                       or (flag == "chrome" and row["name"].startswith("ap-health-"))]
            if holders:
                problems.append(f"{flag} lock held by {', '.join(holders)}")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--go", action="store_true")
    args = parser.parse_args()

    date, tasks = ap.today(), ap.load_tasks()
    if args.task_id == "health":
        task = health_task(date)
        health_brief = ap.SCRIPTS.parent / "references" / "health-brief.md"
        if not health_brief.exists():
            sys.exit("the health module is not installed (references/health-brief.md); see BLUEPRINT.md")
        brief = health_brief.read_text().replace("{date}", date)
        cwd, extra_dirs, repo = ap.ROOT, [], {}
    else:
        task = ap.find_task(tasks, args.task_id)
        if not task:
            sys.exit(f"no task {args.task_id} in the registry")
        brief_path = ap.day_dir(date) / "briefs" / f"{task['id']}.md"
        if not brief_path.exists():
            sys.exit(f"write the brief first: {brief_path}")
        brief = brief_path.read_text()
        repo = ap.config()["repos"].get(task.get("repo") or "")
        if not repo:
            sys.exit(f"task {task['id']} has no known repo ({task.get('repo')}); add it to config.json repos")
        cwd, extra_dirs = ap.expand(repo["path"]), [str(ap.expand(repo["path"]))]

    name = ap.session_name(task["id"])
    flags = task.get("flags") or {}
    plan_mode = flags.get("plan_mode", True)
    account_rule = ACCOUNT_RULE.format(account=repo["account"]) if repo.get("account") else ""
    prompt = brief + PROTOCOL.format(name=name, owner=ap.owner(), cockpit=ap.ROOT, account_rule=account_rule,
                                     plan_rule=PLAN_RULE if plan_mode else NO_PLAN_RULE)

    settings = json.loads((ap.ROOT / ".claude" / "dispatch-settings.json").read_text())
    settings["env"]["AUTOPILOT_TASK"] = name
    settings_path = ap.day_dir(date) / "settings" / f"{name}.json"
    ap.write_json(settings_path, settings)

    command = ["claude", "--bg", "--name", name, "--settings", str(settings_path)]
    command += ["--permission-mode", "plan"] if plan_mode else []
    command += ["--chrome"] if flags.get("chrome") else []
    command += ["--add-dir", *extra_dirs, str(ap.STATE)]
    command += ["--", prompt]

    rows, active = active_rows()
    problems = checks(task, name, active, tasks, cwd)
    print(f"task     {task['id']} · {task.get('title', '')}")
    print(f"cwd      {cwd}")
    print(f"session  {name} · {'plan mode' if plan_mode else 'default mode (read-only task)'}"
          f"{' · chrome' if flags.get('chrome') else ''}{' · local stack' if flags.get('local_stack') else ''}")
    print(f"running  {len(active)}/{ap.config()['max_parallel_background']} autopilot sessions")
    print("command  " + " ".join(part if " " not in part else repr(part[:60] + '…') for part in command))
    for problem in problems:
        print(f"BLOCKED  {problem}")
    if problems:
        sys.exit(2)
    if not args.go:
        print("check ok · run again with --go after the owner's yes")
        return

    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=120,
                            env={**os.environ, "AUTOPILOT_TASK": name})
    output = (result.stdout + result.stderr).strip()
    print(output)
    if result.returncode != 0:
        sys.exit(f"launch failed (exit {result.returncode})")
    stamp = ap.now().isoformat(timespec="seconds")
    background_id = (re.search(r"backgrounded\s*·\s*(\w+)", output) or [None, None])[1]
    record = ap.read_json(ap.SESSIONS / f"{name}.json", {}) or {}
    record.update(name=name, task=task["id"], background_id=background_id, dispatched_at=stamp)
    ap.write_json(ap.SESSIONS / f"{name}.json", record)
    if args.task_id != "health":
        task.update(state="dispatched", session={"name": name, "id": background_id, "dispatched_at": stamp},
                    updated=stamp)
        task.setdefault("history", []).append({"state": "dispatched", "at": stamp})
        ap.save_tasks(tasks)
    ap.render_board_async()
    print(f"dispatched {name} (id {background_id}) · attach: claude attach {background_id} · stop: claude stop {background_id}")


if __name__ == "__main__":
    main()
