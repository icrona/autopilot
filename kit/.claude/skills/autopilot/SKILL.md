---
name: autopilot
description: Personal day cockpit. Runs the morning intake (GitHub, local repos, the configured trackers and adapters), plans and spreads the day in the owner's timezone, dispatches each task as a background Claude Code session in its repo (plan mode, max parallel from config), shows the board, watches PRs and wraps the day up. Use for "/autopilot morning", "dispatch", "status", "what needs me", "add this", "replan", "wrap up the day", "logged in to <system>".
argument-hint: "morning | dispatch <task|next> | status | add <text|link> | replan | wrap | logged-in <system>"
---

# Autopilot cockpit

The cockpit plans, dispatches and watches. Each piece of repo work runs as its own background session (`ap-<id>`) in
its repo; the owner answers that session's prompts by attaching to it (`claude attach <session id>`; the `ap-<id>`
name finds only a running session). The owner's name, hours, repos and sources are in `config.json`.

**Hard rules** (the cockpit's `CLAUDE.md` holds them in full):
- Intake is read-only.
- Every push, PR action, tracker/wiki write, chat or mail message, release, workflow run or artifact publish needs the
  owner's explicit yes for that item, after they have seen the full content.
- Never deploy, merge, log in for them or type credentials.

Scripts live in `scripts/` (run them with `python3`, without `-I`). State is in `<cockpit>/state/`. The board is at
`<cockpit>/board.html`; open it once with `open <cockpit>/board.html` and it refreshes itself every 20 s.

## morning
0. **Awake guard.** Run `scripts/awake.sh`. It's a no-op when the cockpit was started with `bin/autopilot`.
1. **Login preflight** (only if `config.json → logins` is non-empty).
   - For each system: `tabs_context_mcp` (createIfEmpty) → `navigate` to its `url` → `javascript_tool` with its `probe`.
     - If the page only has an SSO button, click it and probe again.
     - If it asks for a password, MFA or any credential, mark it `needs-login`. Never type, ask for or read
       credentials.
   - Write `state/days/<date>/logins.json` as `{"checked_at": "HH:MM", "systems": {"<id>": "ok" | "needs-login"}}`.
   - Tell the owner in one line which systems to log in to, and leave those tabs open.
2. **Intake, in parallel,** following `references/sources.md`:
   - `scripts/intake_github.py` (if `intake.github`);
   - `scripts/local_leftovers.py` (if `intake.local`);
   - `scripts/intake_adapters.py` (every enabled adapter);
   - the MCP reads `references/sources.md` lists (tracker, calendar, mail, chat).

   Write the merged result to `state/days/<date>/intake.json` (`{"meetings": [...], "items": [...], "noise": [...]}`).
3. **Triage into tasks,** following `references/task-types.md` for ids, types, priority, effort, flags, windows and
   links. Bots, stale PRs and FYIs → `noise`. Upsert with `scripts/tasks.py upsert`. New tasks are `inbox`; set the
   ones you propose for today to `planned`.
4. **Plan.**
   - Run `scripts/tasks.py capacity` to get free slots, the interrupt buffer and the windows in local time.
   - Write `state/days/<date>/plan.json` (`{"date", "approved": false, "blocks": [...], "not_today": [...], "notes"}`).
     Block kinds:
     - the owner's time: `triage`, `focus`, `touchpoint`, `meeting`, `lunch`, `login-check`, `wrap`;
     - background runs: `bg`, which can overlap the owner's time.
   - Spread the load:
     - follow the priority order;
     - deep `focus` blocks of 90–120 min for the work the owner drives;
     - start `bg` runs just before a focus block, so their results land at the next boundary;
     - add a `touchpoint` (plan approval ~10 min, result review 20–30 min) for every `bg` run;
     - at most `max_parallel_background` `bg` runs at once, and one `local_stack` and one `chrome` holder at a time;
     - respect every task `window` (`before`/`after` a config window id or `HH:MM`);
     - a `login-check` right before each block that needs a login.
   - Anything that doesn't fit goes to `not_today`.
   - Run `scripts/tasks.py validate-plan` until it has no PROBLEM lines, then `scripts/tasks.py plan-md`, then
     `scripts/render_board.py`.
5. **Present.**
   - Show the logins still needed, the plan table, the not-today list and the noise count.
   - Ask the owner to approve, reorder or drop. On approval, set `"approved": true` and re-render.
   - Then propose the first wave of dispatches as a list. One yes can cover a listed wave.

## dispatch <id|next>
1. For `next`, pick the earliest approved `bg` block whose task isn't dispatched yet.
2. Write `state/days/<date>/briefs/<id>.md` from the template in `references/task-types.md`. Show the owner a 3-line
   summary.
3. Run `scripts/dispatch.py <id>`. It checks the parallel limit, the chrome and local-stack locks and folder trust, and
   prints the exact command.
4. After the owner's yes, run `--go`. Report the session name and `claude attach <session id>` (dispatch.py prints
   the id).

## status
Run `scripts/render_board.py --print`. Lead with Needs you: what each session waits on, how long it has waited, and
its attach command. Then cover logins, the PR watch line, Working, Ready for review and the next planned block. Keep
it short. To ask a running session for a summary, use SendMessage. A message is never consent.

## logged-in <system>
Probe that system again (as in morning step 1) and update `logins.json`. Then SendMessage each session whose
`AP-STATUS` is `needs-login <system>`: "Login to <system> is done. Retry the step that needed it." Re-render the board.

## add <text|link>
Turn the text or link into a task (read the link read-only first), upsert it as `inbox`, and say where it would fit.
If it's P1 or P2, offer `replan`.

## replan
Rebuild the remaining blocks from now until the end of the workday: keep finished and running work, re-validate the
plan, show only what moved and what fell to not-today.

## wrap
1. Run `scripts/tasks.py wrap` for the done items, the carry-over with attach commands, and estimated vs actual time.
2. Draft `state/days/<date>/wrap.md`: done with links; carry-over; "What I learned about my estimates" (one line);
   optionally a standup draft (Yesterday / Today / Blockers).
3. Offer, item by item, any tracker comments or posts. Each one needs a yes and goes through the gates.
4. Warn if `ap-*` sessions are still working: closing the cockpit ends `caffeinate`. Offer
   `caffeinate -ims -t <seconds>`, or `claude stop <id>` for each session (`stop` takes the id, not the name), with a
   yes for each.
5. Mention any chore the owner did by hand twice this week that could become a skill.

## PR watch (conflicts + feedback)
`bin/autopilot` starts `scripts/pr_watch.py --loop` next to the cockpit and kills it on exit (an orphaned watcher exits
within 15 s; a lock keeps it to one). It runs two read-only `gh` GraphQL polls inside working hours only, and skips PRs
idle for more than `stale_idle_days`.

**Standing yes:** auto-dispatch in plan mode is allowed only for the task types the owner turned on in config
(`pr_watch.auto_dispatch` → `conflict-*`, `pr_watch.feedback_auto_dispatch` → `fb-*`). The session still stops at its
plan, and every push and every PR reply still needs a per-item yes.

- **Conflicts** (every `interval_s`): on CONFLICTING it upserts `conflict-<repo>-<pr>` (type `rebase`), writes its
  brief and dispatches it (when allowed). An undispatched conflict task closes when the PR merges cleanly again or
  leaves the watch. Each conflict notifies once.
- **Feedback** (every `feedback_interval_s`): an open thread (unresolved, not outdated, last comment not the owner's)
  or a new review (COMMENTED / CHANGES_REQUESTED with a body, after the head commit) creates `fb-<repo>-<pr>` with a
  brief listing every item. Only ids never seen before trigger. Bots are ignored unless `feedback_include_bots`.
- **Fallback:** if dispatch.py blocks, the task stays in Needs you with the BLOCKED reason in `notes` and is retried on
  the next poll. A repo outside `config.json → repos` or marked `read_only` only notifies.
- **Owner rule:** if an open, dispatched task links the PR or is named after its branch, no new session starts; the
  notification says to attach to that session.
- **Snapshot and log:** `state/pr-watch.json`, `state/pr-watch.log`. Board header:
  `PR watch HH:MM · N conflicting · M with feedback`, or `not running` / the error.

## Gotchas
- Background sessions need the repo folder itself to be trusted (a trusted parent isn't enough). `dispatch.py` blocks
  with the fix: the owner runs `claude` in that folder once and accepts.
- `claude agents --json` reports background sessions as `working` / `blocked` / `done` / `failed` / `stopped`, and
  interactive ones as `idle` / `busy`. A session that reports done keeps its slot until it's stopped.
- Background sessions in a git repo get their own worktree under `.claude/worktrees/`, built from HEAD: commit before
  dispatching into a repo whose uncommitted changes the task needs. `dispatch.py` passes `--add-dir <repo>` so outputs
  can go to the main checkout.
- Run `intake_github.py` and `local_leftovers.py` in the background while you do the MCP reads.
- Chrome in background sessions is not guaranteed; the fallback is to run that step in the cockpit.
