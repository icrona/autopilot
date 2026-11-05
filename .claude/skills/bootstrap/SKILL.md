---
name: bootstrap
description: Generate a personal Autopilot day cockpit from this blueprint repo — interview the owner about their hours, repos, sources, connected MCP servers and notification channel, copy kit/ into the cockpit folder, render config.json, CLAUDE.md, the gate lists and the references for their setup, then verify it locally. Use for "/bootstrap", "set up autopilot", "generate my cockpit", "install the cockpit on this Mac", or "update my cockpit from the blueprint".
argument-hint: "[cockpit dir, default ~/autopilot]"
---

# Bootstrap an Autopilot cockpit

Read `BLUEPRINT.md` first. `kit/` is the reference implementation; you copy it and tailor it — don't rewrite the
scripts from scratch. Everything in this skill writes **local files only**. Nothing outward happens during bootstrap:
no push, no repo creation, no message, no publish.

## 0. Preconditions (check, don't fix)
- macOS (the launcher uses `caffeinate`, the default notifier uses `osascript`). On Linux, say what to replace and
  continue only if the owner agrees.
- `claude --version`, `python3 --version` (≥ 3.9), `git --version`. If `python3` is older, look for a ≥ 3.9
  interpreter that is already installed (`uv python list --only-installed`, Homebrew, pyenv) and offer to call it by
  absolute path in the cockpit only (§2 step 5); install nothing without a yes.
- `gh auth status` if GitHub intake or the PR watch will be on. If not logged in, tell the owner to run
  `! gh auth login` themselves; never handle tokens.
- The target folder: default `~/autopilot`. If it exists and is non-empty, stop and ask: update in place (see §5) or
  pick another folder.

## 1. Interview (AskUserQuestion, at most 4 questions per call, recommended option first)
Collect, with sensible defaults offered:
1. **Owner**: the name used in briefs and notifications; GitHub login (`github.me`); optional search scope
   (`org:<name>` / `user:<name>` / none). If `gh auth status` shows several accounts and the owner works as more than
   one, explain the per-call token read (BLUEPRINT §5, "GitHub identities") and, on a yes, list them in
   `github.accounts` (keep `github.me` = the main one) and give each repo its `account`.
2. **Time**: IANA timezone (read the system one with `readlink /etc/localtime` and offer it), workday start/end,
   lunch, interrupt buffer %, max parallel background sessions (default 3).
3. **Repos**: for each — key, local path (verify it exists and is a git repo), GitHub `owner/name`, base branch,
   `read_only`. Offer to discover candidates with `ls ~/code ~/Documents/GitHub 2>/dev/null` and `git -C <p> remote -v`.
4. **Sources**: GitHub (review requests, my PRs, feedback) on/off; local leftovers on/off; tracker (none / GitHub
   Issues via the example adapter / Linear / Jira / other via MCP); calendar via MCP or ask each morning; Hermes adapter
   slot kept disabled (default) or wired now (then ask how Hermes exposes its data).
5. **Gates**: list the MCP servers actually connected in this session (the tool names visible to you) and propose
   every write/send/create/update/delete/publish tool for the `ask` list and every destructive one for `deny`. Show
   the list; the owner confirms.
6. **Automation**: PR watch on/off (`pr_watch.enabled`); standing yes for auto-dispatching `conflict-*` and/or `fb-*` in plan mode
   (default off — explain that pushes and replies stay gated either way).
7. **Notifications**: macOS (default), ntfy, Hermes slot, or another adapter. A channel that leaves the machine needs
   an explicit yes and the owner's choice of how much task detail goes into messages.
8. **Windows**: any fixed daily deadlines or "not before" times (default none).
9. **Style**: one or two lines on how the owner likes answers (goes into the cockpit's CLAUDE.md "Style").
10. **Login preflight**: any web tools that need an SSO login each morning (default none).

## 2. Generate
Let `C` be the cockpit folder.
1. `cp -R kit/. C/` (keeps `.claude/`, `bin/`, `.gitignore`). Remove `C/config.example.json` after writing config.
2. Write `C/config.json` from `kit/config.example.json` with the interview answers. Keep every key of the example
   (`owner.name`, `timezone`, `workday`, `max_parallel_background`, `touchpoints_min`, `windows`, `repos`, `github`,
   `intake`, `logins`, `cockpit_chrome`, `pr_watch`, `notify`). `touchpoints_min` and `intake.*` are read by the skill
   text, not by scripts. GitHub off ⇒ set `pr_watch.enabled: false`.
3. Render `C/CLAUDE.md`: replace `{{OWNER}}`, `{{TIMEZONE}}`, `{{HOURS}}`, `{{STYLE}}`.
4. Gates: write the confirmed `ask` / `deny` lists into **both** `C/.claude/settings.json` and
   `C/.claude/dispatch-settings.json` (keep `Bash(git push *)` and `Artifact`).
5. Paths: if `C` is not `~/autopilot`, replace `$HOME/autopilot` in both settings files with the absolute path of `C`,
   and add `"AUTOPILOT_HOME": "<C>"` to `dispatch-settings.json → env` (hooks in dispatched sessions run from other
   repos and find the cockpit through it). Also tell the owner to export `AUTOPILOT_HOME=<C>` in their shell profile
   if they will run scripts outside `bin/autopilot`.
   Also add `"env": {"AUTOPILOT_HOME": "<C>"}` to `C/.claude/settings.json`, so a cockpit opened without
   `bin/autopilot` still finds its own folder.
   Interpreter: if the owner chose an absolute ≥ 3.9 interpreter in §0, replace the `python3` command in both
   settings files and in `C/bin/autopilot`, the `#!/usr/bin/env python3` line of the scripts, hooks and enabled Python
   adapters, and the "run them with" line of `C/.claude/skills/autopilot/SKILL.md` with its absolute path; say so in
   `C/CLAUDE.md` and use it for §3.
6. Tailor `C/.claude/skills/autopilot/references/sources.md` (fill the Tracker and Calendar sections with the exact
   read-only MCP queries for the owner's tools; delete sections for sources that are off) and `task-types.md` (types,
   priorities and repo rules that fit the owner's work).
7. Adapters: enable the chosen ones in `config.json → intake.adapters` / `notify.channel`; `chmod +x` them. If Hermes
   is being wired now, replace the stub with a read-only script that satisfies `adapters/README.md`.
8. Optional modules (BLUEPRINT §9): login preflight only needs `config.logins` entries (and `cockpit_chrome: true`).
   A health check needs `references/health-brief.md` and whatever classifier fits the owner's services; design those
   with the owner. The entry point and board tiles already exist.
9. `git -C C init -b main` (git older than 2.28: `git -C C init`, then `git -C C symbolic-ref HEAD refs/heads/main`)
   and an initial local commit (no remote). This is local; still mention it.

## 3. Verify (all local, all read-only towards the outside)
Run from `C` with `AUTOPILOT_HOME=C`:
- `python3 -m unittest discover -s .claude/skills/autopilot/tests` — also with `/usr/bin/python3` if present.
- `python3 .claude/skills/autopilot/scripts/tasks.py capacity`
- Upsert one fake task for a configured repo, write a tiny `plan.json` with one `bg` block, then
  `tasks.py validate-plan`, `tasks.py plan-md`, `render_board.py --print`.
- `dispatch.py <fake id>` **without `--go`** — expect either "check ok" or a BLOCKED line naming an untrusted folder
  (that's the first-run step below). Never run `--go` during bootstrap.
- If GitHub is on: `intake_github.py`, and `pr_watch.py --once --dry-run` **with `AUTOPILOT_HOME` pointing at a
  throwaway copy of `C`** (a dry run still creates `conflict-*`/`fb-*` tasks, briefs and seen ids; it only skips
  notifications and `--go`).
- `intake_adapters.py` for each enabled adapter.
- Remove the fake task, plan and briefs afterwards (`state/` is git-ignored, but leave it clean).
Report each result plainly, including failures.

## 4. Hand-off
Tell the owner, in this order:
1. Trust each repo folder once: `cd <repo> && claude` → accept → exit (background sessions refuse untrusted folders).
2. Start the cockpit: `C/bin/autopilot` (optionally in its own terminal window), then `/autopilot morning`.
3. Open the board once: `open C/board.html`.
4. What is gated, what (if anything) has a standing yes, and where to change it.

## 5. Updating an existing cockpit from a newer blueprint
Diff `kit/` against `C/` file by file. Never overwrite `config.json`, `CLAUDE.md`, the tailored references or
`state/`. Show the owner each script change and apply only what they approve; re-run §3.
