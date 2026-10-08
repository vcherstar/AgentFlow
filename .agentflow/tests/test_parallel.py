"""Project Preflight rule "- parallel: <tool>=<n>": no more than n running attempts of a tool on this machine.
Regression: two Codex sessions at once on Windows locked the sandbox and user accounts (error 1909)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

FLOW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLOW / "tools"))
import tick  # noqa: E402

TASK = """# {tid}: Probe

Role: developer
Tool: codex
Stage: 1
Depends on: none
Branch: {low}-probe
Worktree: {wt}
Independent check: none - probe
Resume: none

## Allowed files

- `src/{low}.txt` - probe

## Acceptance criteria

- [ ] probe passes

## Checks

- `python -c "print(1)"` - probe

## Result
"""


class ParallelRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "p"
        flow = self.root / ".agentflow"
        shutil.copytree(FLOW / "tools", flow / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        (flow / "tasks" / ".runtime").mkdir(parents=True)
        shutil.copy(FLOW / "tasks" / "_template.md", flow / "tasks")
        (flow / "docs").mkdir()
        (flow / "docs" / "project-rules.md").write_text("# Rules\n\n## Preflight\n\n- parallel: codex=1\n", encoding="utf-8")
        for tid in ("T-001", "T-002"):
            (flow / "tasks" / f"{tid}-probe.md").write_text(
                TASK.format(tid=tid, low=tid.lower(), wt=Path(self.tmp.name) / "wt" / tid), encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        self.flow = flow
        for tid in ("T-001", "T-002"):
            self.tool("ledger.py", "add", tid, "--title", "Probe", "--stage", "1", "--role", "developer", "--tool", "codex",
                      "--status", "ready")
        # T-001 runs in codex (the launcher's runtime record)
        (flow / "tasks" / ".runtime" / "T-001.json").write_text(json.dumps(
            {"taskId": "T-001", "attempts": [{"n": 1, "tool": "codex", "status": "running", "pid": 1}]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def tool(self, name, *args):
        p = subprocess.run([sys.executable, str(self.flow / "tools" / name), *args], capture_output=True, text=True,
                           encoding="utf-8", cwd=self.root)
        return p

    def preflight(self, tool, live="T-001"):
        out = Path(self.tmp.name) / "pf.json"
        self.tool("gate.py", "preflight", "T-002", "--live", live, "--tool", tool, "--out", str(out))
        return json.loads(out.read_text(encoding="utf-8"))

    def test_second_codex_is_refused_other_tools_and_free_slots_pass(self):
        pf = self.preflight("codex")
        self.assertFalse(pf["ok"])
        self.assertTrue(any("running session(s) on this machine" in p and "T-001" in p for p in pf["problems"]), pf["problems"])
        self.assertTrue(self.preflight("devin")["ok"], self.preflight("devin")["problems"])
        self.assertTrue(self.preflight("codex", live="")["ok"])

    def test_tick_waits_for_a_slot_instead_of_calling_an_orchestrator(self):
        rt = Path(self.tmp.name) / "rt"
        refused = "T-002 preflight failed: codex allows 1 running session(s) on this machine (project rule); running: T-001"
        with patch.object(tick, "RT", rt), patch.object(tick, "HEARTBEAT", rt / "o.json"), \
                patch.object(tick, "LIMITS", rt / "l.json"), patch.object(tick, "TICK", rt / "t.json"), \
                patch.object(tick, "LOCK", rt / "lock"), \
                patch.object(tick.gate, "ledger_rows", return_value={"T-002": {"Status": "ready", "Tool": "codex"}}), \
                patch.object(tick.gate, "parse", return_value={"id": "T-002", "role": "developer", "depends": [], "independent": "none", "result": ""}), \
                patch.object(tick.gate, "last_attempt", return_value=None):
            rep = tick.run(launch=lambda t, tool: (False, refused))
        self.assertEqual(rep["needs"], [])
        self.assertIn("no free slot", rep["waits"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
