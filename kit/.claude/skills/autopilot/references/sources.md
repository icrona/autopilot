# Intake sources (all read-only)

The bootstrap tailors this file to the sources the owner chose. Run the scripts and MCP reads in parallel. Write the
merged result to `state/days/<date>/intake.json` as `{"meetings": [...], "items": [...], "noise": [...]}`. The scripts
write `intake-github.json`, `intake-local.json` and `intake-<adapter>.json` next to it.

## GitHub: `scripts/intake_github.py` (when `intake.github`)
- **Review requests,** to the owner or to `github.teams`, oldest first. `bot`, `age_days` and `idle_days` help triage:
  bot PRs and PRs idle for more than 30 days go to `noise` unless the owner asks.
- **My open PRs,** with failing and pending checks, review decision and merge state. A failing check is a task; an
  approved and clean PR is a "merge?" question for the owner, never an action. A conflicting PR is `pr_watch.py`'s job.
- **Review feedback on my open PRs:** each `my_prs` row carries `open_threads`, `new_reviews` and `reviewers`.
  `pr_watch.py` creates and dispatches the `fb-<repo>-<pr>` task, so triage only checks it exists. A `feedback_error`
  on a row means the read failed, not that there is none.
- `github.search_scope` (`org:<name>`, `user:<name>` or empty) narrows every search.

`gh` is the source; it must be logged in as `github.me`. With `github.accounts`, every listed login must be logged
in to gh (`gh auth status` lists them, none needs to be the active one); each row carries its `account`, and an error
names the account it belongs to.

## Local: `scripts/local_leftovers.py` (when `intake.local`)
Worktrees, branches with unpushed commits from the last 14 days, recent untracked notes (`*-review.md`,
`*-handover.md`, …), live Claude sessions and the registry's carry-over. A squash-merged branch also shows as
"unpushed": check it against `my_prs` before calling it pending work.

## Adapters: `scripts/intake_adapters.py`
Every adapter in `intake.adapters` (see `adapters/README.md`). An `error` in its file means the source failed, not that
it is empty; say so.

## Tracker (when `intake.tracker` is set)
<!-- The bootstrap fills this in. Examples:
- GitHub Issues: the `example-tracker` adapter.
- Linear / Jira via MCP: the exact read-only queries (assigned to me and not done; my team's current cycle/sprint,
  unassigned or blocked only; watched items updated in the last day). Keep key, title, status, priority, estimate, URL.
Never write. -->

## Calendar (optional, via MCP)
When connected, read today's events in the configured timezone: start, end, title, accepted. Declined and free events
don't count against capacity. When not connected, ask once: "Meetings today? (e.g. 10:00–10:30 standup)".
