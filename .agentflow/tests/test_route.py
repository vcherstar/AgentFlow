"""route.py against a local stand-in for the TypeSafe API: the real HTTP path, no network, no real key."""
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


class Fake(BaseHTTPRequestHandler):
    answer = {}
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append({"auth": self.headers.get("Authorization"), "body": body})
        data = json.dumps({"model": "jev-test", "answers": {"option": Fake.answer},
                           "usage": {"input_tokens": 300, "output_tokens": 30}}).encode()
        self.send_response(200)
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
            "codex-max": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh", "describe": "long tasks"},
            "devin-free": {"tool": "devin", "model": "swe-2-high", "describe": "free", "until": y},
            "agy-later": {"tool": "agy", "describe": "quota back", "from": t},
            "claude": {"tool": "claude", "describe": "architecture", "roles": ["developer"]}}}), encoding="utf-8")
        subprocess.run([sys.executable, flow / "tools" / "ledger.py", "add", "T-001", "--title", "Probe", "--stage", "1",
                        "--role", "developer", "--tool", "claude"], check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def route(self, *args, key=True):
        env = {**os.environ, "TYPESAFE_API_BASE": f"http://127.0.0.1:{self.server.server_port}"}
        env.pop("AGENTFLOW_TYPESAFE_KEY_FILE", None)
        if key:
            env["TYPESAFE_API_KEY"] = KEY
        else:
            env.pop("TYPESAFE_API_KEY", None)
        return subprocess.run([sys.executable, "-X", "utf8", self.root / ".agentflow" / "tools" / "route.py", *args],
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def task(self):
        return (self.root / ".agentflow" / "tasks" / "T-001-probe.md").read_text(encoding="utf-8")

    def test_apply_writes_tool_and_model(self):
        Fake.answer = {"choice": "codex-max", "confidence": 0.9, "probabilities": {"codex-max": 0.9, "claude": 0.1}}
        r = self.route("T-001", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        sent = Fake.seen[-1]
        self.assertEqual(sent["auth"], f"Bearer {KEY}")
        self.assertEqual(sorted(sent["body"]["questions"]["option"]["criteria"]), ["claude", "codex-max"],
                         "expired and not-yet-available options are left out")
        self.assertIn("free first", sent["body"]["state"])
        text = self.task()
        self.assertIn("Tool: codex\nModel: gpt-6.1-sol, effort=xhigh\n", text)
        ledger = (self.root / ".agentflow" / "state" / "tasks.md").read_text(encoding="utf-8")
        self.assertIn("| codex |", ledger)
        self.assertIn("300 in / 30 out tokens, request req_test", r.stdout)
        log = json.loads((self.root / ".agentflow" / "tasks" / ".runtime" / "T-001.route.json").read_text(encoding="utf-8"))
        self.assertEqual((log[-1]["usage"], log[-1]["requestId"]), ({"input_tokens": 300, "output_tokens": 30}, "req_test"))

    def test_low_confidence_changes_nothing(self):
        Fake.answer = {"choice": "claude", "confidence": 0.3, "probabilities": {"claude": 0.6, "codex-max": 0.4}}
        before = self.task()
        r = self.route("T-001", "--apply")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.task(), before)

    def test_no_key_is_not_a_crash(self):
        r = self.route("T-001", key=False)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no TypeSafe key", r.stdout)
        self.assertEqual(Fake.seen, [])

    def test_started_task_is_not_rewritten(self):
        subprocess.run([sys.executable, self.root / ".agentflow" / "tools" / "ledger.py", "set", "T-001", "--status",
                        "in progress"], check=True, capture_output=True)
        Fake.answer = {"choice": "codex-max", "confidence": 0.9, "probabilities": {"codex-max": 0.9}}
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
