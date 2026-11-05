# Autopilot

A blueprint for a personal day cockpit on [Claude Code](https://claude.com/claude-code): one session plans your day,
dispatches each piece of repo work as its own background Claude Code session, watches your PRs, and shows everything
on a local board — while every push, PR action, message or publish still waits for your explicit yes.

- **[BLUEPRINT.md](BLUEPRINT.md)** — the design: principles, architecture, routines, file contracts, gates, adapters,
  and the Claude Code lessons behind them.
- **[kit/](kit/)** — a working reference implementation (Python 3.9+, stdlib only, plus `gh` and `claude`).
- **[.claude/skills/bootstrap](.claude/skills/bootstrap/SKILL.md)** — the generator that turns the kit into *your*
  cockpit.

## Quick start (on a new Mac)
1. Install Claude Code, `gh` (logged in), and clone this repo.
2. Open Claude Code in the clone and run `/bootstrap`. It interviews you (hours, repos, sources, connected MCP
   servers, notifications), generates the cockpit in `~/autopilot` (or a folder you pick), and verifies it locally.
3. Trust each repo folder once (`cd <repo> && claude`, accept), then start the cockpit with `~/autopilot/bin/autopilot`
   and run `/autopilot morning`.

## What it does
- **morning** — read-only intake (GitHub review requests and your PRs, unfinished local work, tracker and calendar
  through MCP, any intake adapters), triage into tasks, a day plan that respects your hours, lunch, meetings, an
  interrupt buffer and parallel-session limits.
- **dispatch** — each task becomes `claude --bg` in its repo, in plan mode, with gated settings and a status protocol.
- **board** — Needs you / Working / Ready for review / timeline, refreshed by hooks.
- **PR watch** — merge conflicts and new review feedback on your PRs become tasks (and, if you allow it, plan-mode
  sessions).
- **wrap** — done, carry-over, estimate vs actual.

## Tests
```sh
cd kit/.claude/skills/autopilot && python3 -m unittest discover -s tests
```

## License
[MIT](LICENSE)
