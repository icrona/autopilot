#!/usr/bin/env python3
"""Read-only scan of unfinished local work: worktrees, unpushed commits, recent untracked notes per repo,
Claude sessions (background and interactive), and open tasks carried over in the registry.
Writes days/<date>/intake-local.json."""
import json
import subprocess
import sys
import time

import ap

NOTE_HINTS = ("review", "handover", "plan", "verification", "validation", "rca", "notes")
RECENT_DAYS = 7


def git(path, *args):
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else ""


def worktrees(path):
    rows, current = [], {}
    for line in git(path, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if current:
                rows.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value or True
    return [{"path": row.get("worktree"), "branch": str(row.get("branch", "")).replace("refs/heads/", ""),
             "head": str(row.get("HEAD", ""))[:9]} for row in rows[1:]]  # rows[0] is the main checkout


def unpushed(path):
    """Branches touched in the last RECENT_DAYS*2 days that hold commits no remote has."""
    cutoff = time.time() - RECENT_DAYS * 2 * 86400
    rows = []
    for line in git(path, "for-each-ref", "--sort=-committerdate", "refs/heads",
                    "--format=%(refname:short) %(committerdate:unix)").splitlines():
        branch, _, stamp = line.rpartition(" ")
        if not stamp.isdigit() or int(stamp) < cutoff:
            break
        ahead = git(path, "rev-list", "--count", branch, "--not", "--remotes")
        if ahead and ahead != "0":
            rows.append({"branch": branch, "unpushed_commits": int(ahead),
                         "last_commit": git(path, "log", "-1", "--format=%ad %s", "--date=short", branch)})
    return rows[:10]


def recent_notes(path):
    cutoff = time.time() - RECENT_DAYS * 86400
    notes = []
    for name in git(path, "ls-files", "--others", "--exclude-standard", "--", "*.md").splitlines():
        full = path / name
        if ".claude/" in name or not full.exists() or full.stat().st_mtime < cutoff:
            continue
        if any(hint.lower() in name.lower() for hint in NOTE_HINTS):
            notes.append({"file": name, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(full.stat().st_mtime))})
    return sorted(notes, key=lambda note: note["modified"], reverse=True)[:15]


def main():
    cfg = ap.config()
    repos = {}
    for name, repo in cfg["repos"].items():
        path = ap.expand(repo["path"])
        if not (path / ".git").exists():
            continue
        status = git(path, "status", "--porcelain=v1", "--untracked-files=no")
        repos[name] = {"branch": git(path, "branch", "--show-current"), "dirty_files": len(status.splitlines()),
                       "worktrees": worktrees(path), "unpushed": unpushed(path), "recent_notes": recent_notes(path)}
    sessions = [{"name": row.get("name"), "kind": row.get("kind"), "cwd": row.get("cwd"),
                 "state": row.get("state") or row.get("status"), "sessionId": row.get("sessionId")}
                for row in ap.agents(include_done=False)]
    carry = [task for task in ap.load_tasks() if task.get("state") not in {"done", "dropped"}]
    result = {"generated": ap.now().isoformat(timespec="seconds"), "repos": repos, "sessions": sessions,
              "carry_over": carry}
    ap.write_json(ap.day_dir() / "intake-local.json", result)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
