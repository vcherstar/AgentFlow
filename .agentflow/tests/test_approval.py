"""A planned task waits for the human's approval: ledger.py add makes it blocked "awaiting approval", nothing launches
it (tick.py, gate.py preflight) until ledger.py approve. Regression: the conductor would have launched a freshly
planned task within minutes, before the human said yes."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

FLOW = Path(__file__).resolve().parents[1]

TASK = """# T-001: Probe

Role: developer
Tool: codex
Stage: 1
Depends on: none
Branch: t-001-probe
Worktree: {wt}
Independent check: none - probe
Resume: none

## Goal

Probe.

## Allowed files

- `src/a.txt` - probe

## Do not touch

- `docs/`

## Acceptance criteria

- [ ] probe passes

## Checks

- `python -c "print(1)"` - probe
"""


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.machine_env = patch.dict(os.environ, {"AGENTFLOW_MACHINE_STATE_DIR": self.tmp.name})
        self.machine_env.start()
        self.root = Path(self.tmp.name) / "p"
        flow = self.root / ".agentflow"
        shutil.copytree(FLOW / "tools", flow / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(FLOW / "tasks" / "_template.md", (flow / "tasks").mkdir(parents=True) or flow / "tasks")
        (flow / "state").mkdir()
        (flow / "tasks" / "T-001-probe.md").write_text(TASK.format(wt=Path(self.tmp.name) / "wt" / "p-t-001-probe"), encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        self.flow = flow

    def tearDown(self):
        self.machine_env.stop()
        self.tmp.cleanup()

    def tool(self, name, *args, check=True):
        p = subprocess.run([sys.executable, str(self.flow / "tools" / name), *args], capture_output=True, text=True,
                           encoding="utf-8", cwd=self.root)
        if check:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def row(self):
        return self.tool("ledger.py", "show", "T-001").stdout.split(" | ")

    def preflight(self):
        out = Path(self.tmp.name) / "pf.json"
        self.tool("gate.py", "preflight", "T-001", "--out", str(out), check=False)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_planned_task_waits_until_approved(self):
        self.tool("ledger.py", "add", "T-001", "--title", "Probe", "--stage", "1", "--role", "developer", "--tool", "codex",
                  "--notes", "why")
        row = self.row()
        self.assertEqual(row[5], "blocked")
        self.assertEqual(row[8], "awaiting approval; why")
        pf = self.preflight()
        self.assertFalse(pf["ok"])
        self.assertTrue(any("approval" in p for p in pf["problems"]), pf["problems"])
        tick = json.loads(self.tool("tick.py", "run", "--dry-run", "--json").stdout)
        self.assertEqual(tick["would"], [])
        self.assertIn("approval", tick["waits"][0]["detail"])

        self.tool("ledger.py", "approve", "T-001")
        row = self.row()
        self.assertEqual((row[5], row[8]), ("ready", "why"))
        self.assertTrue(self.preflight()["ok"], self.preflight()["problems"])
        tick = json.loads(self.tool("tick.py", "run", "--dry-run", "--json").stdout)
        self.assertEqual([w["task"] for w in tick["would"]], ["T-001"])

    def test_explicit_ready_needs_no_approval_and_approve_refuses_it(self):
        self.tool("ledger.py", "add", "T-001", "--title", "Successor", "--stage", "1", "--role", "developer",
                  "--tool", "codex", "--status", "ready")
        self.assertEqual(self.row()[5], "ready")
        p = self.tool("ledger.py", "approve", "T-001", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not awaiting approval", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
