"""Fixture tests for pr_watch.py (conflict + feedback polls) and the board's PR watch line.

  python3 -m unittest discover -s .claude/skills/autopilot/tests -v

Everything runs on a throwaway AUTOPILOT_HOME: the real state/ is never touched, nothing reaches GitHub, notifications
are recorded instead of shown, and dispatch.py is only ever run without --go."""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(tempfile.mkdtemp(prefix="aphome-"))
(HOME / ".claude").mkdir()
shutil.copy(HERE / "fixture-config.json", HOME / "config.json")  # fixed fake repos, never the real config
(HOME / ".claude" / "dispatch-settings.json").write_text('{"env": {}}')
os.environ["AUTOPILOT_HOME"] = str(HOME)  # before ap is imported: ap.ROOT is read at import time
sys.path.insert(0, str(HERE.parent / "scripts"))

import ap  # noqa: E402
import pr_watch  # noqa: E402
import render_board  # noqa: E402
import tasks  # noqa: E402

REAL_NOW = ap.now
ME = "me-handle"


class Clock:
    """ap.now() that moves one minute per poll, so two polls never share a `since` stamp."""

    def __init__(self):
        self.moment = REAL_NOW().replace(microsecond=0)

    def tick(self):
        self.moment += dt.timedelta(minutes=1)

    def __call__(self):
        return self.moment


def iso_utc(moment):
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def author(login, bot=False):
    return {"login": login, "__typename": "Bot" if bot else "User"}


def comment(login, body, url, bot=False):
    return {"body": body, "url": url, "createdAt": "2026-11-01T00:00:00Z", "author": author(login, bot)}


def thread(tid, by="rev-a", body="Please rename this field", path="src/a.py", line=42, replies=(),
           resolved=False, outdated=False, bot=False):
    comments = [comment(by, body, f"https://github.com/x/pull/59#discussion_{tid}", bot)]
    comments += [comment(login, text, f"https://github.com/x/pull/59#discussion_{tid}_{i}", login.endswith("[bot]"))
                 for i, (login, text) in enumerate(replies)]
    return {"id": tid, "isResolved": resolved, "isOutdated": outdated, "path": path, "line": line,
            "originalLine": line, "comments": {"totalCount": len(comments), "nodes": comments},
            "last": {"nodes": [{"author": comments[-1]["author"]}]}}


def review(rid, by="rev-a", state="COMMENTED", body="A few things to look at", submitted="2026-11-01T12:00:00Z",
           bot=False):
    return {"id": rid, "state": state, "body": body, "url": f"https://github.com/x/pull/59#pullrequestreview-{rid}",
            "submittedAt": submitted, "author": author(by, bot)}


def fb_node(clock, repo="acme/api", number=59, branch="ENG-52", threads=(), reviews=(),
            head_date="2026-11-01T06:00:00Z", draft=False):
    return {"number": number, "title": f"ENG-52 rules {number}", "url": f"https://github.com/{repo}/pull/{number}",
            "isDraft": draft, "updatedAt": iso_utc(clock() - dt.timedelta(hours=1)), "headRefName": branch,
            "headRefOid": "c" * 40, "baseRefName": "main", "repository": {"nameWithOwner": repo},
            "commits": {"nodes": [{"commit": {"committedDate": head_date}}]},
            "reviews": {"nodes": list(reviews)}, "reviewThreads": {"totalCount": len(threads), "nodes": list(threads)}}


def conflict_node(repo, number, state, branch, updated, base="main"):
    return {"number": number, "title": f"T{number}", "url": f"https://github.com/{repo}/pull/{number}",
            "isDraft": False, "updatedAt": updated, "mergeable": state,
            "reviewDecision": "APPROVED" if number == 640 else None, "headRefName": branch, "headRefOid": "a" * 40,
            "baseRefName": base, "repository": {"nameWithOwner": repo}}


class WatchTest(unittest.TestCase):
    real_dispatch = staticmethod(pr_watch.dispatch)

    def setUp(self):
        shutil.rmtree(ap.STATE, ignore_errors=True)
        ap.SESSIONS.mkdir(parents=True)
        ap.save_tasks([])
        self.clock = Clock()
        ap.now = self.clock
        self.sent, self.dispatched = [], []
        ap.notify = self.record_notify
        ap.render_board_async = lambda: None
        pr_watch.dispatch = self.fake_dispatch
        self.cfg = ap.config()
        self.cfg["pr_watch"].update(auto_dispatch=True, feedback_auto_dispatch=True, feedback_include_bots=False)
        self.fixtures = Path(tempfile.mkdtemp(dir=HOME))

    def tearDown(self):
        ap.now = REAL_NOW

    def record_notify(self, message, kind="normal", key=None, title="Autopilot"):
        if key in {sent_key for sent_key, _ in self.sent}:  # ap.notify dedupes by key
            return False
        self.sent.append((key, message))
        return True

    def fake_dispatch(self, task_id, dry_run):
        self.dispatched.append((task_id, dry_run))
        return False, "not dispatched: 3 autopilot sessions already running (max 3)"

    def fixture(self, nodes):
        path = self.fixtures / f"{len(list(self.fixtures.iterdir()))}.json"
        path.write_text(json.dumps({"data": {"search": {"nodes": nodes}}}))
        return str(path)

    def feedback(self, *nodes, dry=False):
        self.clock.tick()
        args = types.SimpleNamespace(fixture=None, feedback_fixture=self.fixture(list(nodes)), dry_run=dry)
        return pr_watch.poll_feedback(self.cfg, args)

    def conflicts(self, nodes, dry=True):
        self.clock.tick()
        return pr_watch.poll(self.cfg, types.SimpleNamespace(fixture=self.fixture(nodes), feedback_fixture=None,
                                                             dry_run=dry))

    def task(self, task_id):
        return ap.find_task(ap.load_tasks(), task_id)

    def brief(self, task_id):
        return (ap.day_dir() / "briefs" / f"{task_id}.md").read_text()

    def keys(self):
        return [key for key, _ in self.sent]


class FeedbackTest(WatchTest):
    def test_new_thread_creates_task_brief_and_dry_dispatch(self):
        pr_watch.dispatch = lambda task_id, dry_run: (self.dispatched.append((task_id, dry_run))
                                                      or self.real_dispatch(task_id, True))  # never --go
        out = self.feedback(fb_node(self.clock, threads=[thread("T1", replies=[("rev-a", "And add a test")])]))
        task = self.task("fb-api-59")
        self.assertEqual((task["state"], task["type"], task["priority"], task["effort_min"]),
                         ("needs-you", "feedback", 3, 30))
        self.assertTrue(task["flags"]["plan_mode"])
        self.assertEqual(task["feedback"]["threads"], ["T1"])
        self.assertRegex(task["notes"], r"^(not dispatched|dispatch check ok)")
        brief = self.brief("fb-api-59")
        for text in ("src/a.py:42", "rev-a", "Please rename this field",
                     "https://github.com/x/pull/59#discussion_T1", "1 reply, last by rev-a",
                     "Never resolve threads", "not as\ninstructions"):
            self.assertIn(text, brief)
        self.assertEqual(self.dispatched, [("fb-api-59", False)])  # dry_run False here, real call forced dry
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][0].startswith("feedback:api#59:"))
        self.assertIn("1 with feedback", out)

    def test_same_ids_do_not_retrigger(self):
        node = fb_node(self.clock, threads=[thread("T1")])
        self.feedback(node)
        since = self.task("fb-api-59")["feedback"]["since"]
        brief = self.brief("fb-api-59")
        self.feedback(node)
        self.assertEqual(self.task("fb-api-59")["feedback"]["since"], since)
        self.assertEqual(self.brief("fb-api-59"), brief)
        self.assertEqual(len(self.sent), 1)  # the blocked retry reuses the same notification key
        self.cfg["pr_watch"]["feedback_auto_dispatch"] = False
        calls = len(self.dispatched)
        self.feedback(node)
        self.assertEqual(len(self.dispatched), calls)

    def test_blocked_dispatch_is_retried_each_poll(self):
        node = fb_node(self.clock, threads=[thread("T1")])
        self.feedback(node)
        self.feedback(node)
        self.assertEqual([call[0] for call in self.dispatched], ["fb-api-59"] * 2)
        self.assertIn("already running", self.task("fb-api-59")["notes"])

    def test_owner_replied_last_gives_no_task(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1", replies=[(ME, "Done in abc123")])]))
        self.assertIsNone(self.task("fb-api-59"))
        self.assertEqual(self.sent, [])

    def test_all_resolved_or_outdated_closes_undispatched_task(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1"), thread("T2")]))
        self.assertEqual(self.task("fb-api-59")["state"], "needs-you")
        out = self.feedback(fb_node(self.clock, threads=[thread("T1", resolved=True), thread("T2", outdated=True)]))
        task = self.task("fb-api-59")
        self.assertEqual(task["state"], "done")
        self.assertIn("feedback cleared on GitHub (no open feedback)", task["notes"])
        self.assertIn("closed its feedback task", out)

    def test_dispatched_task_is_not_closed(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1")]))
        tasks = ap.load_tasks()
        ap.find_task(tasks, "fb-api-59").update(state="dispatched", session={"name": "ap-fb-api-59"})
        ap.save_tasks(tasks)
        self.feedback(fb_node(self.clock, threads=[thread("T1", resolved=True)]))
        self.assertEqual(self.task("fb-api-59")["state"], "dispatched")

    def test_owner_rule_notifies_only(self):
        ap.save_tasks([{"id": "ENG-52", "state": "dispatched", "session": {"name": "ap-ENG-52"}, "links": []}])
        self.feedback(fb_node(self.clock, threads=[thread("T1")]))
        self.assertIsNone(self.task("fb-api-59"))
        self.assertEqual(self.dispatched, [])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][0].endswith(":owned"))
        self.assertIn("ap-ENG-52 is on this PR: attach", self.sent[0][1])

    def test_own_running_session_gets_notified_not_rewritten(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1")]))
        tasks = ap.load_tasks()
        ap.find_task(tasks, "fb-api-59").update(state="dispatched", session={"name": "ap-fb-api-59"})
        ap.save_tasks(tasks)
        brief = self.brief("fb-api-59")
        self.feedback(fb_node(self.clock, threads=[thread("T1"), thread("T2")]))
        task = self.task("fb-api-59")
        self.assertEqual((task["state"], task["session"]["name"]), ("dispatched", "ap-fb-api-59"))
        self.assertEqual(self.brief("fb-api-59"), brief)
        self.assertTrue(self.keys()[-1].endswith(":owned"))
        self.assertEqual(len(self.dispatched), 1)

    def test_new_id_after_seen_retriggers(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1")]))
        first = self.task("fb-api-59")["feedback"]["since"]
        self.feedback(fb_node(self.clock, threads=[thread("T1"), thread("T2", by="rev-b", body="Typo here")]))
        task = self.task("fb-api-59")
        self.assertNotEqual(task["feedback"]["since"], first)
        self.assertEqual(task["feedback"]["threads"], ["T1", "T2"])
        self.assertIn("Typo here", self.brief("fb-api-59"))
        self.assertEqual(len(self.sent), 2)

    def test_bot_only_gives_no_task_unless_included(self):
        node = fb_node(self.clock, threads=[thread("T1", by="copilot-pull-request-reviewer", bot=True)],
                       reviews=[review("R1", by="copilot-pull-request-reviewer", bot=True),
                                review("R2", by="dependabot[bot]")])
        self.feedback(node)
        self.assertIsNone(self.task("fb-api-59"))
        self.assertEqual(self.sent, [])
        self.cfg["pr_watch"]["feedback_include_bots"] = True
        self.feedback(node)
        self.assertEqual(self.task("fb-api-59")["feedback"]["reviews"], ["R1", "R2"])

    def test_reviews_count_only_after_head_commit_with_body(self):
        self.feedback(fb_node(self.clock, reviews=[review("R0", submitted="2026-11-01T05:00:00Z"),
                                                   review("R1", body="  "), review("R2", state="APPROVED"),
                                                   review("R3", by=ME)]))
        self.assertIsNone(self.task("fb-api-59"))
        self.feedback(fb_node(self.clock, reviews=[review("R4", state="CHANGES_REQUESTED", body="Needs a test")]))
        self.assertEqual(self.task("fb-api-59")["feedback"]["reviews"], ["R4"])
        self.assertIn("rev-a (CHANGES_REQUESTED)", self.brief("fb-api-59"))

    def test_unknown_and_read_only_repos_notify_only(self):
        self.feedback(fb_node(self.clock, repo="acme/other-repo", number=9, threads=[thread("T1")]),
                      fb_node(self.clock, repo="acme/docs", number=7, threads=[thread("T2")]))
        self.assertEqual([task["id"] for task in ap.load_tasks()], [])
        self.assertEqual(sorted(key.split(":")[1] for key in self.keys()), ["docs#7", "other-repo#9"])
        self.assertTrue(all(key.endswith(":notify-only") for key in self.keys()))

    def test_pr_leaving_watch_closes_task(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1")]))
        out = self.feedback()
        self.assertEqual(self.task("fb-api-59")["state"], "done")
        self.assertIn("left the watch", out)

    def test_stale_and_draft_prs_are_skipped(self):
        stale = fb_node(self.clock, threads=[thread("T1")])
        stale["updatedAt"] = "2025-01-01T00:00:00Z"
        self.feedback(stale, fb_node(self.clock, number=60, threads=[thread("T2")], draft=True))
        self.assertEqual(ap.load_tasks(), [])

    def test_dry_run_does_not_notify(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1")]), dry=True)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.dispatched, [("fb-api-59", True)])

    def test_fetch_error_keeps_last_section(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1")]))
        self.clock.tick()
        args = types.SimpleNamespace(fixture=None, feedback_fixture=str(self.fixtures / "missing.json"), dry_run=False)
        out = pr_watch.poll_feedback(self.cfg, args)
        section = ap.read_json(pr_watch.WATCH_FILE)["feedback"]
        self.assertIn("feedback error", out)
        self.assertIn("api#59", section["prs"])
        self.assertIn("error_at", section)

    def test_polls_keep_each_others_sections(self):
        self.feedback(fb_node(self.clock, threads=[thread("T1")]))
        self.conflicts([conflict_node("acme/web", 640, "MERGEABLE", "ENG-50", iso_utc(self.clock()))])
        snapshot = ap.read_json(pr_watch.WATCH_FILE)
        self.assertIn("api#59", snapshot["feedback"]["prs"])
        self.assertIn("web#640", snapshot["prs"])
        self.feedback(fb_node(self.clock, threads=[thread("T1")]))
        self.assertIn("web#640", ap.read_json(pr_watch.WATCH_FILE)["prs"])

    def test_feedback_due(self):
        self.assertTrue(pr_watch.feedback_due(self.cfg))
        self.feedback()
        self.assertFalse(pr_watch.feedback_due(self.cfg))
        self.clock.moment += dt.timedelta(seconds=self.cfg["pr_watch"]["feedback_interval_s"])
        self.assertTrue(pr_watch.feedback_due(self.cfg))


class IntakeTest(unittest.TestCase):
    def test_pr_feedback_fields(self):
        cfg = ap.config()
        clock = Clock()
        node = fb_node(clock, threads=[thread("T1", replies=[(ME, "why?"), ("rev-a", "because")])],
                       reviews=[review("R1", by="rev-b")])
        fb = pr_watch.intake_github.pr_feedback(node, cfg)
        self.assertEqual([item["id"] for item in fb["open_threads"]], ["T1"])
        self.assertEqual(fb["open_threads"][0]["replies"], 2)
        self.assertEqual(fb["open_threads"][0]["last_author"], "rev-a")
        self.assertEqual([item["id"] for item in fb["new_reviews"]], ["R1"])
        self.assertEqual(fb["reviewers"], ["rev-a", "rev-b"])


class AccountsTest(unittest.TestCase):
    """Several GitHub logins of one owner: each is read with its own token, both count as the owner."""

    def setUp(self):
        self.intake = pr_watch.intake_github
        self.real_run, self.calls = self.intake.subprocess.run, []
        self.intake.subprocess.run = self.fake_run
        self.intake._tokens.clear()
        self.cfg = ap.config()
        self.cfg["github"]["accounts"] = [{"me": "work-me", "search_scope": "org:acme"}, {"me": "home-me"}]
        self.fail = set()

    def tearDown(self):
        self.intake.subprocess.run = self.real_run
        self.intake._tokens.clear()

    def fake_run(self, command, **kwargs):
        self.calls.append((command, (kwargs.get("env") or {}).get("GH_TOKEN")))
        if command[:3] == ["gh", "auth", "token"]:
            login = command[command.index("--user") + 1]
            return types.SimpleNamespace(returncode=1 if login in self.fail else 0, stderr="no such login",
                                         stdout="" if login in self.fail else f"tok-{login}\n")
        search = next(part for part in command if part.startswith("q="))
        number = 1 if "author:work-me" in search else 2
        nodes = [None, {"url": f"https://github.com/x/y/pull/{number}", "number": number},
                 {"url": "https://github.com/x/y/pull/9", "number": 9}]  # 9 is found under both logins
        return types.SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"data": {"search": {"nodes": nodes}}}))

    def test_single_login_reads_as_the_active_account(self):
        cfg = ap.config()
        self.assertEqual(self.intake.accounts(cfg), [{"me": ME, "search_scope": "org:acme", "teams": [], "via": None}])
        self.assertEqual(self.intake.logins(cfg), {ME})
        self.intake.search_nodes(cfg, "query", "is:pr is:open")
        self.assertEqual([token for _, token in self.calls], [None])  # no token is read or passed
        self.assertIn(f"q=is:pr is:open author:{ME} org:acme", self.calls[0][0])

    def test_each_listed_account_is_read_with_its_own_token(self):
        nodes = self.intake.search_nodes(self.cfg, "query", "is:pr is:open")
        self.assertEqual([node["number"] for node in nodes], [1, 9, 2])  # merged, the shared PR once
        searches = [(next(part for part in command if part.startswith("q=")), token)
                    for command, token in self.calls if command[:2] == ["gh", "api"]]
        self.assertEqual(searches, [("q=is:pr is:open author:work-me org:acme", "tok-work-me"),
                                    ("q=is:pr is:open author:home-me", "tok-home-me")])
        self.assertEqual(self.intake.logins(self.cfg), {"work-me", "home-me"})

    def test_one_unreadable_account_fails_the_whole_watch_read(self):
        self.fail = {"home-me"}
        with self.assertRaisesRegex(RuntimeError, "gh has no login for home-me"):
            self.intake.search_nodes(self.cfg, "query", "is:pr is:open")

    def test_a_reply_from_any_own_login_is_the_owners(self):
        node = fb_node(Clock(), threads=[thread("T1", replies=[("home-me", "done")]),
                                         thread("T2", by="work-me", body="note to self"),
                                         thread("T3", replies=[("work-me", "why?"), ("rev-a", "because")])],
                       reviews=[review("R1", by="home-me"), review("R2", by="rev-b")])
        fb = self.intake.pr_feedback(node, self.cfg)
        self.assertEqual([item["id"] for item in fb["open_threads"]], ["T3"])
        self.assertEqual([item["id"] for item in fb["new_reviews"]], ["R2"])


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.real_config = ap.config

    def tearDown(self):
        ap.config = self.real_config

    def slots(self, workday):
        ap.config = lambda: {**self.real_config(), "workday": workday}
        return [(tasks.hhmm(start), tasks.hhmm(stop)) for start, stop in tasks.free_slots("2026-01-05")]

    def test_lunch_splits_the_day(self):
        self.assertEqual(self.slots({"start": "09:00", "end": "18:00", "lunch": {"start": "12:30", "end": "13:30"}}),
                         [("09:00", "12:30"), ("13:30", "18:00")])

    def test_a_workday_without_lunch_is_one_slot(self):
        self.assertEqual(self.slots({"start": "19:00", "end": "23:00", "lunch": None}), [("19:00", "23:00")])
        self.assertEqual(self.slots({"start": "19:00", "end": "23:00"}), [("19:00", "23:00")])


class ConflictRegressionTest(WatchTest):
    """The 7-step conflict harness."""

    def test_seven_steps(self):
        recent = iso_utc(self.clock())
        base = [conflict_node("acme/infra", 94, "CONFLICTING", "ENG-44", recent, base="master"),
                conflict_node("acme/other-repo", 9, "CONFLICTING", "x", recent),
                conflict_node("acme/docs", 7, "CONFLICTING", "y", recent, base="master"),
                conflict_node("acme/web", 501, "CONFLICTING", "old", "2025-02-01T00:00:00Z")]
        web = lambda state: base + [conflict_node("acme/web", 640, state, "ENG-50", recent)]
        task_id = "conflict-web-640"

        out = self.conflicts(web("MERGEABLE"), dry=False)
        self.assertIn("4 watched, 3 conflicting", out)
        self.assertIsNone(self.task(task_id))
        self.assertEqual(self.task("conflict-infra-94")["state"], "needs-you")
        self.assertEqual(sorted(key.split(":")[1] + ":" + key.split(":")[-1] for key in self.keys()),
                         ["docs#7:notify-only", "infra#94:blocked", "other-repo#9:notify-only"])

        self.conflicts(web("CONFLICTING"))
        task = self.task(task_id)
        since = task["conflict"]["since"]
        self.assertEqual((task["state"], task["type"], task["priority"]), ("needs-you", "rebase", 3))
        self.assertIn("git push --force-with-lease=ENG-50", self.brief(task_id))

        self.conflicts(web("CONFLICTING"))
        self.assertEqual(self.task(task_id)["conflict"]["since"], since)

        self.conflicts(web("UNKNOWN"))
        self.assertEqual((self.task(task_id)["state"], self.task(task_id)["conflict"]["since"]), ("needs-you", since))

        out = self.conflicts(web("MERGEABLE"))
        self.assertEqual(self.task(task_id)["state"], "done")
        self.assertIn("conflict cleared on GitHub (mergeable again) before dispatch", self.task(task_id)["notes"])
        self.assertIn("merges cleanly again", out)

        self.conflicts(web("CONFLICTING"))
        self.assertEqual(self.task(task_id)["state"], "needs-you")
        self.assertNotEqual(self.task(task_id)["conflict"]["since"], since)

        out = self.conflicts(base)
        self.assertEqual(self.task(task_id)["state"], "done")
        self.assertIn("PR closed or merged", self.task(task_id)["notes"])
        self.assertIn("left the watch", out)
        self.assertTrue(all(task_id == call[0] or call[0] == "conflict-infra-94" for call in self.dispatched))
        self.assertTrue(all(dry for _, dry in self.dispatched[1:]))


class BoardLineTest(unittest.TestCase):
    def setUp(self):
        self.cfg = ap.config()
        self.checked = ap.now().isoformat(timespec="seconds")

    def line(self, watch):
        return render_board.pr_watch_line({"pr_watch": watch, "cfg": self.cfg})

    def test_without_feedback_section(self):
        text, warn = self.line({"checked_at": self.checked, "prs": {"a#1": {"mergeable": "CONFLICTING"}}})
        self.assertEqual(text, f"PR watch {self.checked[11:16]} · 1 conflicting")
        self.assertFalse(warn)

    def test_with_feedback_section(self):
        watch = {"checked_at": self.checked, "prs": {},
                 "feedback": {"checked_at": self.checked, "prs": {"a#1": {"open": ["T1"]}, "b#2": {"open": []}}}}
        self.assertEqual(self.line(watch), (f"PR watch {self.checked[11:16]} · 0 conflicting · 1 with feedback", False))

    def test_feedback_error_warns(self):
        later = (ap.now() + dt.timedelta(minutes=1)).isoformat(timespec="seconds")
        watch = {"checked_at": self.checked, "prs": {},
                 "feedback": {"checked_at": self.checked, "prs": {}, "error": "gh: boom", "error_at": later}}
        text, warn = self.line(watch)
        self.assertIn("feedback error", text)
        self.assertTrue(warn)


if __name__ == "__main__":
    unittest.main()
