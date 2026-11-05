"""Table test for .claude/hooks/outward_guard.py: outward commands must return "ask", read-only and local ones pass."""
import importlib.util
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parents[3] / "hooks" / "outward_guard.py"  # tests → autopilot → skills → .claude
spec = importlib.util.spec_from_file_location("outward_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

ASK = [
    "git push origin HEAD:feature",
    "git -c core.sshCommand=x push --force-with-lease",
    "gh pr create --title x --body y",
    "gh pr comment 12 --body hi",
    "gh pr merge 12",
    "gh issue comment 3 --body x",
    "gh release create v1.0.0",
    "gh workflow run deploy.yml",
    "gh run rerun 123",
    "gh api repos/o/r/issues/1/comments -f body=x",
    "gh api -X PATCH repos/o/r/pulls/1",
    "gh api graphql -f query='mutation { addComment(input: {}) { clientMutationId } }'",
    "curl -X POST https://api.example.com/things -d '{}'",
    "curl --json '{}' https://hooks.example.com/x",
    "docker push me/image:1",
    "npm publish",
    "kubectl apply -f x.yaml",
]
PASS = [
    "git status && git log --oneline -5",
    "git fetch origin && git rebase origin/main",
    "gh pr view 12 --json title",
    "gh pr list --author @me",
    "gh api repos/o/r/pulls/1",
    "gh api graphql -f query='query { viewer { login } }'",
    "curl -s https://api.example.com/things",
    "curl -X POST http://localhost:8080/things -d '{}'",
    "python3 -m unittest",
]


class GuardTest(unittest.TestCase):
    def test_outward_commands_ask(self):
        for command in ASK:
            with self.subTest(command=command):
                self.assertIsNotNone(guard.reason_for(command))

    def test_local_and_read_only_commands_pass(self):
        for command in PASS:
            with self.subTest(command=command):
                self.assertIsNone(guard.reason_for(command))


if __name__ == "__main__":
    unittest.main()
