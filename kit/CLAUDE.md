# Autopilot cockpit

This folder is {{OWNER}}'s day cockpit. The cockpit plans and spreads the day ({{TIMEZONE}}, {{HOURS}}), dispatches
each piece of repo work as its own background Claude Code session in that repo, and shows everything on a local board.
The `/autopilot` skill (`.claude/skills/autopilot/SKILL.md`) holds the routines; `config.json` holds hours, windows,
repos, sources and notification settings.

## Hard rules
- **Nothing outward without a per-item yes.** That covers a push, any PR action (create, edit, comment, review,
  merge), a tracker or wiki write, a chat or mail message (send, schedule or draft), a release, a workflow run or
  rerun, and an artifact or doc publish.
  - Show {{OWNER}} the exact content or command first, then wait for an explicit yes for that item.
  - Approving a plan or a dispatch wave is not consent to any of these.
  - The settings in `.claude/` back this rule with `ask` rules and the `outward_guard.py` hook. Never work around
    them.
- **Never deploy or merge.** {{OWNER}} does that.
- **Read-only intake.** Every source is only read.
- **Logins belong to {{OWNER}}.** Clicking an SSO button is fine. On a password, MFA or any credential prompt, stop and
  say so. Never type, ask for or read credentials, and never switch to another auth path without a yes.
- **Facts only:** open every link before listing it; say what a check does not prove; never invent numbers.
- **Discretion.** Board and state files stay local. Don't copy task details into anything that leaves this machine
  unless {{OWNER}} asks.

## Layout
- `bin/autopilot` starts the cockpit under `caffeinate -ims`, plus `pr_watch.py --loop`, which it kills on exit.
- `.claude/settings.json` holds the cockpit's gates; `.claude/dispatch-settings.json` is layered on every dispatched
  session (gates plus the `session_event.py` hooks that feed the board).
- `.claude/hooks/`: `outward_guard.py` (PreToolUse on Bash returns "ask") and `session_event.py` (records state, sends
  urgent notifications, re-renders the board).
- `.claude/skills/autopilot/`: the skill, its references, scripts, adapters and tests.
- `state/tasks.json` is the registry; `state/days/<date>/` holds intake, plan, briefs and wrap; `state/sessions/` the
  per-session hook records; `state/pr-watch.json` the PR watch snapshot.
- `board.html` is the generated board (local only).

## Style
{{STYLE}}
