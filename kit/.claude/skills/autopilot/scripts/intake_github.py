#!/usr/bin/env python3
"""Read-only GitHub intake through `gh`: review requests (me and my teams), my open PRs with checks and the review
feedback waiting on them (shared with pr_watch.py). Writes days/<date>/intake-github.json.

`github.search_scope` in config.json (e.g. "org:my-org" or "user:me", empty for everything) narrows every search.
`github.accounts` lists several logins of the same owner (`[{"me", "search_scope", "teams"}]`): each one is read with
its own token, which `gh auth token --user <login>` hands over in memory for the next call only (never written, never
printed, the active gh account is not switched). Without it the single `github.me` is read as gh's active account."""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import ap

SEARCH_FIELDS = "number,title,repository,url,author,createdAt,updatedAt,isDraft"
BOT_LOGINS = {"copilot-pull-request-reviewer"}
FEEDBACK_REVIEW_STATES = {"COMMENTED", "CHANGES_REQUESTED"}
FEEDBACK_QUERY = """query($q: String!) { search(query: $q, type: ISSUE, first: 50) { nodes { ... on PullRequest {
  number title url isDraft updatedAt headRefName headRefOid baseRefName repository { nameWithOwner }
  commits(last: 1) { nodes { commit { committedDate } } }
  reviews(last: 30) { nodes { id state body url submittedAt author { login __typename } } }
  reviewThreads(first: 50) { totalCount nodes { id isResolved isOutdated path line originalLine
    comments(first: 10) { totalCount nodes { body url createdAt author { login __typename } } }
    last: comments(last: 1) { nodes { author { login __typename } } } } } } } } }"""


TOKEN_TTL_S = 300
_tokens = {}


def accounts(cfg):
    """The GitHub identities to read: every entry of github.accounts (each through its own token, `via`), or the
    single top-level github.me through gh's active account (`via` None)."""
    github = cfg["github"]
    listed = github.get("accounts") or []
    if listed:
        return [{"me": account["me"], "search_scope": (account.get("search_scope") or "").strip(),
                 "teams": account.get("teams") or [], "via": account["me"]} for account in listed]
    return [{"me": github["me"], "search_scope": (github.get("search_scope") or "").strip(),
             "teams": github.get("teams") or [], "via": None}]


def logins(cfg):
    """Every login that is the owner: their own comments and PRs never count as someone waiting on them."""
    return {account["me"] for account in accounts(cfg)}


def token(login):
    """The login's token from gh's own store, kept in memory for a few minutes at most."""
    cached = _tokens.get(login)
    if cached and time.time() - cached[1] < TOKEN_TTL_S:
        return cached[0]
    result = subprocess.run(["gh", "auth", "token", "--user", login], capture_output=True, text=True, timeout=30)
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise RuntimeError(f"gh has no login for {login}: the owner runs `gh auth login` for that account")
    _tokens[login] = (value, time.time())
    return value


def gh(*args, account=None):
    """Run a read-only gh command; `account` (a login from github.accounts) selects whose token it runs with."""
    env = {**os.environ, "GH_TOKEN": token(account)} if account else None
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=90, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}…{f' as {account}' if account else ''}: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout or "null")


def days_since(iso):
    return (ap.now() - dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))).days


def scope_args(account):
    """`gh search prs` takes the scope qualifier as its query argument."""
    return [account["search_scope"]] if account["search_scope"] else []


def search_scope(account):
    return f" {account['search_scope']}" if account["search_scope"] else ""


def search_nodes(cfg, query, filters):
    """One read-only GraphQL search per account for its own PRs (`filters` plus its author and scope), merged. Any
    account failing fails the whole read, so a PR never looks closed just because one login could not be read."""
    seen, nodes = set(), []
    for account in accounts(cfg):
        search = f"{filters} author:{account['me']}{search_scope(account)}"
        data = gh("api", "graphql", "-f", f"query={query}", "-f", f"q={search}", account=account["via"])
        for node in data["data"]["search"]["nodes"]:
            if node and node["url"] not in seen:
                seen.add(node["url"])
                nodes.append(node)
    return nodes


def pr_row(item):
    return {"repo": item["repository"]["nameWithOwner"], "number": item["number"], "title": item["title"],
            "url": item["url"], "author": item["author"]["login"], "bot": item["author"].get("is_bot") or item["author"]["login"].endswith("[bot]"),
            "created": item["createdAt"], "updated": item["updatedAt"], "draft": item["isDraft"],
            "age_days": days_since(item["createdAt"]), "idle_days": days_since(item["updatedAt"])}


def review_requests(cfg, account):
    """Open PRs waiting for this account's review (or its teams'); the owner's own PRs under any login don't count."""
    seen, rows, mine = set(), [], logins(cfg)
    for reviewer in ["@me", *account["teams"]]:
        for item in gh("search", "prs", *scope_args(account), f"--review-requested={reviewer}", "--state=open",
                       "--json", SEARCH_FIELDS, "--limit", "50", account=account["via"]):
            key = (item["repository"]["nameWithOwner"], item["number"])
            if key not in seen and item["author"]["login"] not in mine:
                seen.add(key)
                rows.append({**pr_row(item), "requested_via": "me" if reviewer == "@me" else reviewer,
                             "account": account["me"]})
    return rows


def my_pr_state(row, via=None):
    detail = gh("pr", "view", str(row["number"]), "-R", row["repo"], "--json",
                "statusCheckRollup,reviewDecision,mergeStateStatus,headRefName,headRefOid", account=via)
    checks = detail.get("statusCheckRollup") or []
    failing = [check.get("name") or check.get("context") for check in checks
               if (check.get("conclusion") or check.get("state") or "").upper() in {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED"}]
    pending = [check.get("name") or check.get("context") for check in checks
               if (check.get("status") or "").upper() in {"IN_PROGRESS", "QUEUED", "PENDING"} or (check.get("state") or "").upper() == "PENDING"]
    return {**row, "branch": detail.get("headRefName"), "head": (detail.get("headRefOid") or "")[:9],
            "review_decision": detail.get("reviewDecision"), "merge_state": detail.get("mergeStateStatus"),
            "checks": {"total": len(checks), "failing": failing, "pending": pending}}


def is_bot(author):
    author = author or {}
    login = author.get("login") or ""
    return author.get("__typename") == "Bot" or login in BOT_LOGINS or login.endswith("[bot]")


def feedback_nodes(cfg, fixture=None):
    """The owner's open non-draft PRs with review threads, last reviews and head commit date: one read-only GraphQL call."""
    if fixture:
        nodes = ap.read_json(fixture)["data"]["search"]["nodes"]
    else:
        nodes = search_nodes(cfg, FEEDBACK_QUERY, "is:pr is:open draft:false")
    return [node for node in nodes if node and not node.get("isDraft")]


def pr_feedback(node, cfg):
    """Feedback waiting for the owner on one PR: unresolved, current threads whose last comment isn't theirs, and review
    bodies (COMMENTED / CHANGES_REQUESTED) submitted after the head commit. Bots count only with feedback_include_bots."""
    mine = logins(cfg)
    include_bots = (cfg.get("pr_watch") or {}).get("feedback_include_bots", False)

    def counts(author):
        return (author or {}).get("login") not in mine and (include_bots or not is_bot(author))

    threads = []
    for thread in (node.get("reviewThreads") or {}).get("nodes") or []:
        comments = (thread.get("comments") or {}).get("nodes") or []
        last = ((((thread.get("last") or {}).get("nodes") or [None])[0]) or {}).get("author") or {}
        if thread.get("isResolved") or thread.get("isOutdated") or not comments or last.get("login") in mine:
            continue
        if not any(counts(comment.get("author")) for comment in comments):
            continue  # only the owner and bots spoke in this thread
        first = comments[0]
        total = (thread.get("comments") or {}).get("totalCount") or len(comments)
        threads.append({"id": thread["id"], "path": thread.get("path"),
                        "line": thread.get("line") or thread.get("originalLine"),
                        "author": (first.get("author") or {}).get("login"), "url": first.get("url"),
                        "body": first.get("body") or "", "replies": max(total - 1, 0),
                        "last_author": last.get("login")})
    commits = (node.get("commits") or {}).get("nodes") or []
    head_date = ((commits[0] or {}).get("commit") or {}).get("committedDate") if commits else None
    reviews = [{"id": review["id"], "state": review["state"], "author": (review.get("author") or {}).get("login"),
                "url": review.get("url"), "body": review.get("body") or "", "submitted": review.get("submittedAt")}
               for review in (node.get("reviews") or {}).get("nodes") or []
               if review and review.get("state") in FEEDBACK_REVIEW_STATES and (review.get("body") or "").strip()
               and counts(review.get("author")) and review.get("submittedAt")
               and (not head_date or review["submittedAt"] > head_date)]
    reviewers = sorted({item["author"] for item in threads + reviews if item["author"]})
    return {"open_threads": threads, "new_reviews": reviews, "reviewers": reviewers}


def my_prs(cfg, account):
    rows = [{**pr_row(item), "account": account["me"]}
            for item in gh("search", "prs", *scope_args(account), "--author=@me", "--state=open",
                           "--json", SEARCH_FIELDS, "--limit", "50", account=account["via"])]
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(lambda row: my_pr_state(row, account["via"]), rows))


def with_feedback(cfg, rows):
    """Add the review feedback waiting on each of the owner's open PRs."""
    try:
        feedback = {(node["repository"]["nameWithOwner"], node["number"]): pr_feedback(node, cfg)
                    for node in feedback_nodes(cfg)}
    except Exception as error:  # the PR list still stands without its feedback
        return [{**row, "feedback_error": str(error)[:200]} for row in rows]
    for row in rows:
        found = feedback.get((row["repo"], row["number"])) or {}  # drafts aren't read: no feedback
        row["open_threads"] = [{**item, "body": item["body"][:300]} for item in found.get("open_threads", [])]
        row["new_reviews"] = [{**item, "body": item["body"][:300]} for item in found.get("new_reviews", [])]
        row["reviewers"] = found.get("reviewers", [])
    return rows


def main():
    cfg = ap.config()
    result, errors, identities = {"generated": ap.now().isoformat(timespec="seconds")}, {}, accounts(cfg)
    for key, fetch in (("review_requests", review_requests), ("my_prs", my_prs)):
        rows, seen, failed = [], set(), 0
        for account in identities:
            try:
                found = fetch(cfg, account)
            except Exception as error:  # report per source and account; one failing query must not hide the others
                errors[key if len(identities) == 1 else f"{key} ({account['me']})"] = str(error)
                failed += 1
                continue
            for row in found:
                if (row["repo"], row["number"]) not in seen:
                    seen.add((row["repo"], row["number"]))
                    rows.append(row)
        if failed < len(identities):  # a source that failed for every account has no rows key, only its error
            result[key] = sorted(rows, key=lambda row: row["created"]) if key == "review_requests" else rows
    if "my_prs" in result:
        result["my_prs"] = with_feedback(cfg, result["my_prs"])
    if errors:
        result["errors"] = errors
    ap.write_json(ap.day_dir() / "intake-github.json", result)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
