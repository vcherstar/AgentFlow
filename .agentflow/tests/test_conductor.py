"""conductor.ps1 end to end in real PowerShell, with a fake tick.py and fake tools (skipped where pwsh is missing).
A real round opens a short-lived window: the fake tool prints a usage limit and exits."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")

FAKE_TICK = r'''
import json, os, sys
from pathlib import Path
rt = Path(__file__).resolve().parents[1] / "tasks" / ".runtime"
rt.mkdir(parents=True, exist_ok=True)
with open(rt / "calls.txt", "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]).replace("\n", " ") + "\n")
if sys.argv[1] == "run":
    print((rt / "report.json").read_text(encoding="utf-8"))
elif sys.argv[1] == "next-orchestrator":
    print(os.environ.get("FAKE_NEXT", ""))
'''

FAKE_TOOL = "@echo off\r\necho args: %*\r\necho ERROR: You've hit your usage limit. Try again at 3:30 PM.\r\nexit /b 1\r\n"
FAKE_DISABLED_TOOL = ("@echo off\r\necho args: %*\r\n"
                      "echo Your organization has disabled Claude subscription access for Claude Code. "
                      "Use an Anthropic API key instead, or ask your admin to enable access\r\nexit /b 1\r\n")

NEED = {"task": "T-7", "action": "need", "detail": "tester Outcome completed, Verdict fail: decide on T-6"}


@unittest.skipUnless(PWSH and os.name == "nt", "PowerShell 7 on Windows needed")
class ConductorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        tools = self.root / ".agentflow" / "tools"
        tools.mkdir(parents=True)
        (self.root / ".agentflow" / "state").mkdir()
        shutil.copy(FLOW / "tools" / "conductor.ps1", tools)
        shutil.copy(FLOW / "tools" / "conductor-panel.ps1", tools)
        shutil.copy(FLOW / "tools" / "machine_capacity.py", tools)
        (tools / "tick.py").write_text(FAKE_TICK, encoding="utf-8")
        self.rt = self.root / ".agentflow" / "tasks" / ".runtime"
        self.rt.mkdir(parents=True)
        for tool in ("codex", "devin"):
            (self.root / f"fake-{tool}.cmd").write_text(FAKE_TOOL, encoding="ascii")
        (self.root / "fake-claude.cmd").write_text(FAKE_DISABLED_TOOL, encoding="ascii")

    def tearDown(self):
        for _ in range(20):  # a session window may still hold its log for a moment
            try:
                self.tmp.cleanup()
                return
            except OSError:
                time.sleep(0.5)

    def report(self, needs=(), live=None, acted=()):
        rep = {"at": "2026-10-08T12:00:00Z", "reportOnly": False, "live": live, "acted": list(acted), "would": [],
               "needs": list(needs), "waits": [], "nextOrchestrator": "", "limited": {}}
        (self.rt / "report.json").write_text(json.dumps(rep), encoding="utf-8")

    def conduct(self, *args, next_tool="codex"):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTFLOW_")}
        env.update({"AGENTFLOW_PYTHON": sys.executable, "FAKE_NEXT": next_tool,
                    "AGENTFLOW_MACHINE_STATE_DIR": str(self.root / "machine"),
                    "AGENTFLOW_CLAUDE": str(self.root / "fake-claude.cmd"),
                    "AGENTFLOW_CODEX": str(self.root / "fake-codex.cmd"), "AGENTFLOW_DEVIN": str(self.root / "fake-devin.cmd")})
        r = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(self.root / ".agentflow" / "tools" / "conductor.ps1"), "-Once", "-NoNotify", *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("round failed", r.stdout + r.stderr)
        return r.stdout

    def state(self):
        p = self.rt / "conductor.json"
        return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}

    def wait_finished(self, n):
        for _ in range(120):
            s = self.state().get("session") or {}
            if s.get("n") == n and s.get("finishedAt"):
                return s
            time.sleep(0.5)
        self.fail(f"session {n} did not finish: {self.state()}")

    def calls(self):
        p = self.rt / "calls.txt"
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def test_dry_run_only_says_what_it_would_do(self):
        self.report(needs=[NEED])
        out = self.conduct("-DryRun")
        self.assertIn("would start a background Orchestrator on codex", out)
        self.assertEqual(self.state(), {})
        self.assertIn("run --json --dry-run", self.calls())

    def test_a_live_orchestrator_is_left_alone(self):
        self.report(needs=[NEED], live={"holder": "claude", "at": "2026-10-08T12:00:00Z"})
        out = self.conduct()
        self.assertIn("claude is live", out)
        self.assertNotIn("session", self.state())

    def test_nothing_to_judge_starts_nothing(self):
        self.report()
        self.conduct()
        self.assertNotIn("session", self.state())

    def test_busy_machine_slot_waits_and_retries_after_release(self):
        self.report(needs=[NEED])
        env = dict(os.environ, AGENTFLOW_MACHINE_STATE_DIR=str(self.root / "machine"))
        helper = FLOW / "tools" / "machine_capacity.py"
        claim = subprocess.run([sys.executable, str(helper), "claim", "codex", str(self.root), "other-project",
                                "--pid", str(os.getpid())], capture_output=True, text=True, env=env, check=True)
        token = claim.stdout.strip()
        try:
            out = self.conduct()
            self.assertIn("waiting for shared machine capacity", out)
            self.assertNotIn("session", self.state())
            self.assertFalse(self.state().get("needsKey"))
        finally:
            subprocess.run([sys.executable, str(helper), "release", token], env=env, check=True)
        self.conduct()
        self.assertEqual(self.wait_finished(1)["tool"], "codex")

    def test_hand_over_then_next_tool_after_a_usage_limit(self):
        self.report(needs=[NEED])
        self.conduct()
        s = self.wait_finished(1)
        self.assertEqual(s["tool"], "codex")
        self.assertTrue(s["limitHit"])
        log = Path(s["log"]).read_text(encoding="utf-8", errors="replace")
        self.assertIn("exec", log)
        self.assertIn("--sandbox danger-full-access", log)
        self.assertIn("holder name 'codex-background'", log)
        self.assertIn("Autonomous orchestration", log)
        calls = self.calls()
        self.assertTrue(any(c.startswith("limit codex") for c in calls), calls)
        self.assertIn("release --holder codex-background", calls)

        out = self.conduct(next_tool="devin")  # same needs, but the last session hit a limit: hand over at once
        self.assertIn("Background Orchestrator (codex) finished", out)
        s = self.wait_finished(2)
        self.assertEqual(s["tool"], "devin")
        self.assertIn("--permission-mode dangerous", Path(s["log"]).read_text(encoding="utf-8", errors="replace"))

    def test_disabled_subscription_access_hands_over_to_the_next_tool(self):
        self.report(needs=[NEED])
        self.conduct(next_tool="claude")
        s = self.wait_finished(1)
        self.assertEqual(s["tool"], "claude")
        self.assertTrue(s["limitHit"])
        self.assertIn("disabled Claude subscription access",
                      Path(s["log"]).read_text(encoding="utf-8", errors="replace"))
        calls = self.calls()
        self.assertTrue(any(c.startswith("limit claude") for c in calls), calls)

        out = self.conduct(next_tool="codex")
        self.assertIn("Background Orchestrator (claude) finished", out)
        s = self.wait_finished(2)
        self.assertEqual(s["tool"], "codex")

    def test_same_needs_wait_for_the_cooldown(self):
        self.report(needs=[NEED])
        st = {"sessions": 1, "needsKey": None, "reportedFinish": 1,
              "session": {"n": 1, "tool": "codex", "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "finishedAt": "x", "limitHit": False, "pid": None}}
        (self.rt / "conductor.json").write_text(json.dumps(st), encoding="utf-8")
        out = self.conduct("-DryRun")
        self.assertIn("would start", out)  # other needs than last time: hand over
        # the key a real hand-over stores for these needs: the same needs must then wait
        key = hashlib.sha256(f"{NEED['task']}|{NEED['detail']}".encode()).hexdigest()[:16].upper()
        st["needsKey"] = key
        (self.rt / "conductor.json").write_text(json.dumps(st), encoding="utf-8")
        out = self.conduct("-DryRun")
        self.assertIn("same needs were handed over", out)

    def panel_status(self, shell):
        r = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(self.root / ".agentflow" / "tools" / "conductor-panel.ps1"), "-Status"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return r.returncode, r.stdout + r.stderr

    def test_one_conductor_per_project_and_the_panel_sees_it(self):
        self.report()
        self.assertEqual(self.panel_status(PWSH)[0], 1)
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTFLOW_")}
        env["AGENTFLOW_PYTHON"] = sys.executable
        first = subprocess.Popen([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                  str(self.root / ".agentflow" / "tools" / "conductor.ps1"), "-NoNotify",
                                  "-IntervalMinutes", "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        try:
            for _ in range(60):
                if (self.state().get("conductor") or {}).get("lastRound"):
                    break
                time.sleep(0.5)
            self.assertEqual(self.state()["conductor"]["pid"], first.pid)
            out = self.conduct()
            self.assertIn("already running", out)
            self.assertNotIn("need", out)
            for shell in filter(None, (PWSH, shutil.which("powershell"))):
                code, text = self.panel_status(shell)
                self.assertEqual(code, 0, text)
            self.assertIn("Сторож: работает", self.panel_status(PWSH)[1])
        finally:
            first.kill()
            first.wait()
        self.assertEqual(self.panel_status(PWSH)[0], 1)

    def test_all_tools_limited_starts_nothing(self):
        self.report(needs=[NEED])
        out = self.conduct(next_tool="")
        self.assertIn("all tools limited", out)
        self.assertNotIn("session", self.state())


if __name__ == "__main__":
    unittest.main()
