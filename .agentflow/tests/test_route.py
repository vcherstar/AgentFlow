"""route.py against a local stand-in for the TypeSafe API: the real HTTP path, no network, no real key.
Request and answer shapes follow https://docs.typesafe.ai (api, choice, score, confidence)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
KEY = "test-key-never-printed"
TODAY = date.today()


def option(choice, confidence, probabilities):
    return {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": probabilities}


def complexity(score, confidence=0.8):
    return {"type": "score", "score": score, "confidence": confidence,
            "probabilities": {"0": 0.1, "1": 0.3, "2": 0.6}, "legend": {"0": "Small", "1": "Moderate", "2": "Hard"}}


class Fake(BaseHTTPRequestHandler):
    answers = {}
    status = 200
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append({"auth": self.headers.get("Authorization"), "body": body})
        if Fake.status != 200:
            data = json.dumps({"error": "rejected"}).encode()
        else:
            data = json.dumps({"model": "jev-1.13.0", "answers": Fake.answers,
                               "usage": {"input_tokens": 300, "output_tokens": 30}}).encode()
        self.send_response(Fake.status)
        self.send_header("x-typesafe-request-id", "req_test")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


TASK = """# T-001: Probe

Role: developer
Tool: claude
Stage: 1
Depends on: none
Branch: t-001-probe
Worktree: W:/nowhere/t-001
Independent check: none - probe
Resume: none

## Goal

Add a settings switch.

## Allowed files

- `src/`

## Acceptance criteria

- [ ] The switch persists.

## Checks

- `git --version` - git runs

## Result
"""


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Fake.seen.clear()
        Fake.status = 200
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "p"
        flow = self.root / ".agentflow"
        shutil.copytree(FLOW / "tools", flow / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        (flow / "tasks").mkdir(parents=True)
        (flow / "state").mkdir()
        (flow / "docs").mkdir()
        (flow / "tasks" / "T-001-probe.md").write_text(TASK, encoding="utf-8")
        y, t = (TODAY - timedelta(days=1)).isoformat(), (TODAY + timedelta(days=1)).isoformat()
        (flow / "docs" / "model-options.json").write_text(json.dumps({"policy": "free first", "options": {
            "codex-max": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh", "what": "long tasks",
                          "not_for": "tiny edits", "examples": ["rewrite the queue"]},
            "devin-high": {"tool": "devin", "model": "swe-2-high", "what": "most tasks", "max_complexity": 1,
                           "escalate_to": "devin-max"},
            "devin-max": {"tool": "devin", "model": "swe-2-max", "what": "hardest tasks"},
            "devin-old": {"tool": "devin", "model": "swe-1", "what": "expired", "until": y},
            "agy-later": {"tool": "agy", "what": "quota back", "from": t},
            "claude": {"tool": "claude", "describe": "architecture (old describe field)", "roles": ["developer"]}}}),
            encoding="utf-8")
        subprocess.run([sys.executable, flow / "tools" / "ledger.py", "add", "T-001", "--title", "Probe", "--stage", "1",
                        "--role", "developer", "--tool", "claude"], check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def route(self, *args, key=True):
        env = {k: v for k, v in os.environ.items() if not k.startswith("TYPESAFE_")}  # never the real service
        env["TYPESAFE_BASE_URL"] = f"http://127.0.0.1:{self.server.server_port}"
        env.pop("AGENTFLOW_TYPESAFE_KEY_FILE", None)
        if key:
            env["TYPESAFE_API_KEY"] = KEY
        return subprocess.run([sys.executable, "-X", "utf8", self.root / ".agentflow" / "tools" / "route.py", *args],
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def task(self):
        return (self.root / ".agentflow" / "tasks" / "T-001-probe.md").read_text(encoding="utf-8")

    def log(self):
        return json.loads((self.root / ".agentflow" / "tasks" / ".runtime" / "T-001.route.json").read_text(encoding="utf-8"))

    def test_request_follows_the_documented_shape(self):
        Fake.answers = {"option": option("codex-max", 0.95, {"codex-max": 0.96, "claude": 0.04}), "complexity": complexity(0.4)}
        r = self.route("T-001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertEqual(len(Fake.seen), 1, "the call reached the local stand-in, not the network")
        sent = Fake.seen[-1]
        self.assertEqual(sent["auth"], f"Bearer {KEY}")
        body = sent["body"]
        self.assertEqual(body["model"], "jev-1.13.0", "the Jev version is pinned")
        self.assertIsInstance(body["state"], dict, "state is an object with named fields")
        self.assertEqual(body["state"]["task"]["goal"], "Add a settings switch.")
        self.assertEqual(sorted(body["questions"]), ["complexity", "option"], "two atomic questions in one request")
        self.assertEqual(body["questions"]["complexity"]["type"], "score")
        self.assertEqual(len(body["questions"]["complexity"]["criteria"]), 3)
        crit = body["questions"]["option"]["criteria"]
        self.assertEqual(sorted(crit), ["claude", "codex-max", "devin-high", "devin-max"],
                         "expired and not-yet-available options are left out")
        self.assertEqual(crit["codex-max"]["not_for"], "tiny edits")
        self.assertEqual(crit["claude"]["what"], "architecture (old describe field)")
        self.assertEqual(body["questions"]["option"]["instructions"]["project_policy"], "free first")
        self.assertIn("300 in / 30 out tokens, request req_test", r.stdout)
        self.assertEqual(self.log()[-1]["requestId"], "req_test")

    def test_high_band_apply_writes_tool_and_model(self):
        Fake.answers = {"option": option("codex-max", 0.95, {"codex-max": 0.96, "claude": 0.04}), "complexity": complexity(0.4)}
        r = self.route("T-001", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Tool: codex\nModel: gpt-6.1-sol, effort=xhigh\n", self.task())
        self.assertIn("| codex |", (self.root / ".agentflow" / "state" / "tasks.md").read_text(encoding="utf-8"))

    def test_medium_band_is_shown_not_applied(self):
        Fake.answers = {"option": option("claude", 0.7, {"claude": 0.8, "codex-max": 0.2}), "complexity": complexity(0.4)}
        before = self.task()
        r = self.route("T-001", "--apply")
        self.assertEqual(r.returncode, 4)
        self.assertIn("medium confidence", r.stdout)
        self.assertEqual(self.task(), before)

    def test_low_band_is_no_decision(self):
        Fake.answers = {"option": option("claude", 0.3, {"claude": 0.5, "codex-max": 0.5}), "complexity": complexity(0.4)}
        before = self.task()
        r = self.route("T-001", "--apply")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.task(), before)

    def test_hard_task_escalates_to_the_stronger_option(self):
        Fake.answers = {"option": option("devin-high", 0.95, {"devin-high": 0.96, "devin-max": 0.04}),
                        "complexity": complexity(1.8)}
        r = self.route("T-001", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("escalated to devin-max", r.stdout)
        self.assertIn("Tool: devin\nModel: swe-2-max\n", self.task())
        self.assertEqual((self.log()[-1]["jevChoice"], self.log()[-1]["choice"]), ("devin-high", "devin-max"))

    def test_moderate_task_stays(self):
        Fake.answers = {"option": option("devin-high", 0.95, {"devin-high": 0.96, "devin-max": 0.04}),
                        "complexity": complexity(1.2)}
        r = self.route("T-001")
        self.assertNotIn("escalated", r.stdout)
        self.assertEqual(self.log()[-1]["choice"], "devin-high")

    def test_no_key_and_api_errors_are_not_crashes(self):
        r = self.route("T-001", key=False)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no TypeSafe key", r.stdout)
        self.assertEqual(Fake.seen, [])
        Fake.status = 422
        r = self.route("T-001")
        self.assertEqual(r.returncode, 3)
        self.assertIn("HTTP 422", r.stdout)
        self.assertIn("request req_test", r.stdout)

    def test_started_task_is_not_rewritten(self):
        subprocess.run([sys.executable, self.root / ".agentflow" / "tools" / "ledger.py", "set", "T-001", "--status",
                        "in progress"], check=True, capture_output=True)
        Fake.answers = {"option": option("codex-max", 0.95, {"codex-max": 0.96}), "complexity": complexity(0.4)}
        before = self.task()
        r = self.route("T-001", "--apply")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.task(), before)

    def test_tester_never_gets_the_developer_tool(self):
        sys.path.insert(0, str(self.root / ".agentflow" / "tools"))
        import route  # noqa: E402  (the copy's module, for the pure filter)
        opts = {"a": {"tool": "codex"}, "b": {"tool": "claude"}, "c": {"tool": "codex", "roles": ["developer"]}}
        self.assertEqual(sorted(route.available(opts, "tester", avoid_tool="claude")), ["a"])
        self.assertEqual(sorted(route.available({"x": {"tool": "claude"}}, "tester", avoid_tool="claude")), ["x"],
                         "the only option stays when nothing else is left")


if __name__ == "__main__":
    unittest.main()
