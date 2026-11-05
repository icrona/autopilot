# Task types, ids, defaults and brief templates

The bootstrap tailors this file to the owner's work. Keep the registry fields, the id rules for `conflict-*` / `fb-*`
and the brief template; change the types, priorities and repo rules freely.

## Registry fields
`id, title, type, repo, links[{label,url}], priority (1–6), effort_min, window {before|after: <window id or HH:MM>},
flags {chrome, local_stack, plan_mode}, state, session {name, id}, notes`

States: `inbox → planned → dispatched → needs-you / ready-for-review / blocked → done | dropped | deferred`.

**Ids:**
- the tracker key for tickets (`ENG-12`), or `issue-<repo>-<n>` for GitHub issues;
- `rev-<repo>-<pr>` for reviews;
- `conflict-<repo>-<pr>` for merge conflicts on the owner's PRs (created only by `pr_watch.py`);
- `fb-<repo>-<pr>` for review feedback on the owner's PRs (created only by `pr_watch.py`);
- `<slug>` for anything else.

The session name is always `ap-<id>`.

**Priority (the planner orders by it):**
1. something broken that the owner owns;
2. time-windowed work (a deadline today);
3. someone blocked on the owner (review requests, oldest first);
4. committed tickets;
5. questions and support;
6. ideas and backlog.

## Types
| Type | Repo | Default effort | Flags | Gate notes |
|---|---|---|---|---|
| `ticket` | from the ticket | estimate × 60–120 min, or ask | `local_stack` if it needs the local stack | test-first; commit locally; push and draft PR only after a yes |
| `review` | the PR's repo | 30–120 min by diff size | — | writes `<id>-review.md` at the repo root (main checkout); never comments on the PR |
| `rebase` | the PR's repo | 30 min | `plan_mode` | auto-dispatched by pr_watch when allowed; force-push only after a yes |
| `feedback` | the PR's repo | 30 min, P3 | `plan_mode` | auto-dispatched by pr_watch when allowed; push and each reply need their own yes; never resolve threads or edit the PR |
| `ops` | the service's repo | 20–40 min | `chrome` if it reads dashboards | read-only; `plan_mode: false` is allowed |
| `support` | the repo the question is about | 30 min | — | reply draft verified against the code; posting needs a yes |
| `writing` | none, done in the cockpit | 20 min | — | content shown first, written after a yes |
| `meeting` | none | from the calendar | — | capacity only, never dispatched |

**Repo rules:** repos marked `read_only` in `config.json` are reference only: never dispatched, never pushed.

## Brief template (`state/days/<date>/briefs/<id>.md`)
`dispatch.py` appends the autopilot protocol (gates, logins, the `AP-STATUS` line). The brief covers only the task:

```markdown
# <id>: <title>

## Task
<one paragraph: what to do and why, in the owner's terms>

## Sources
- <tracker / PR / message links>

## Done means
- <acceptance, as checkable bullets>

## Repo and branch
<repo> · base <base branch> · work branch <id> (worktree is automatic for background sessions)

## How
- <skills to use>
- <type-specific rules>
- Your project memory rules (house formats, code style) apply; read them if unsure.

## Out of scope
- <what not to touch>
```

**Per-type reminders for the "How" section:**
- **ticket:** test first (the new test must fail on the old code); run the affected suite and check it really ran
  tests; run `/code-review` before asking to push; short commit body; the PR body describes the end state.
- **review:** run the PR's tests on its head; reproduce any claimed regression; retract findings that verification
  disproves; draft any reply; post nothing.
- **ops:** read-only; open every link before you list it; separate chronic from new and ours from theirs.
- **support:** verify every claim against the code, quote file:line, keep the reply short.
