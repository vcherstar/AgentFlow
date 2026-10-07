"""accept.py runs its steps in order and stops at the first failure (no real git state is touched)."""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

FLOW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLOW / "tools"))
import accept  # noqa: E402

SHA = "a" * 40
DEV = {"id": "T-900", "role": "developer", "branch": "t-900-probe", "worktree": "W:/nowhere/t-900",
       "result": f"Outcome: completed\nChange: {SHA}\n"}
TESTER = {"id": "T-901", "role": "tester", "sha": SHA, "result": "Outcome: completed\nVerdict: pass\n"}


class AcceptTests(unittest.TestCase):
    def go(self, task, fail_on=None, status="in progress", merged=False):
        calls = []

        def run(cmd, dry, cwd=None, check=True):
            text = " ".join(str(c) for c in cmd)
            calls.append(text)
            if fail_on and fail_on in text and check:
                raise accept.Stop(f"step failed: {text}")
            return ""

        def git_ok(*args, **kw):
            if args[0] == "merge-base":
                return merged
            return True  # the branch exists

        out = io.StringIO()
        with patch.object(accept, "run", run), patch.object(accept.gate, "parse", return_value=dict(task)), \
                patch.object(accept.gate, "main_branch", return_value="master"), \
                patch.object(accept.gate, "git", side_effect=lambda *a, **k: "" if a[0] == "status" else SHA), \
                patch.object(accept.gate, "git_ok", side_effect=git_ok), \
                patch.object(accept, "ledger_rows", return_value={task["id"]: {"Status": status, "Title": "Probe"}}), \
                contextlib.redirect_stdout(out):
            try:
                accept.accept(task["id"], dry=False)
                stopped = None
            except accept.Stop as e:
                stopped = str(e)
        return calls, stopped

    def test_developer_steps_in_order(self):
        calls, stopped = self.go(DEV)
        self.assertIsNone(stopped)
        order = ["gate.py verify", "git merge --no-ff t-900-probe", "--status review", "--status done --commit " + SHA,
                 "git worktree prune", "git branch -d t-900-probe"]
        idx = [next(i for i, c in enumerate(calls) if o in c) for o in order]
        self.assertEqual(idx, sorted(idx), calls)
        self.assertFalse(any("branch -D" in c or "--force" in c for c in calls))

    def test_verify_failure_stops_before_merge(self):
        calls, stopped = self.go(DEV, fail_on="gate.py verify")
        self.assertIsNotNone(stopped)
        self.assertFalse(any("git merge" in c or "ledger.py" in c for c in calls))

    def test_merge_failure_is_aborted_and_ledger_untouched(self):
        calls, stopped = self.go(DEV, fail_on="merge --no-ff")
        self.assertIn("aborted", stopped)
        self.assertTrue(any("merge --abort" in c for c in calls))
        self.assertFalse(any("ledger.py" in c or "worktree" in c or "branch -d" in c for c in calls))

    def test_tester_has_no_merge(self):
        calls, stopped = self.go(TESTER, status="review")
        self.assertIsNone(stopped)
        self.assertFalse(any("git merge" in c for c in calls))
        self.assertTrue(any("--status done --commit " + SHA in c for c in calls))
        self.assertFalse(any("--status review" in c for c in calls))

    def test_already_merged_branch_is_not_merged_again(self):
        calls, _ = self.go(DEV, merged=True)
        self.assertFalse(any("git merge --no-ff" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
