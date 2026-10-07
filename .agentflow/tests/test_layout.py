"""Exercise relocated storage, product paths and dashboard links without launching agents."""
import contextlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

FLOW = Path(__file__).resolve().parents[1]
REPO = FLOW.parent
sys.path.insert(0, str(FLOW / "tools"))
import gate
import ledger

spec = importlib.util.spec_from_file_location("workflow_dashboard", FLOW / "dashboard" / "build.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)

TASK = """# T-900: Layout probe
Role: developer
Tool: codex
Stage: 1
Depends on: none
Branch: t-900-layout-probe
Worktree: unused-probe-directory
Independent check: none - pure local layout probe
## Goal
Check relocated paths without launching a worker.
## Allowed files
- apps/chrome-extension/
## Do not touch
- apps/backend/
## Setup
- copy: README.md
## Acceptance criteria
- [ ] Product setup path resolves from the repository root.
## Checks
- `LAYOUT_PROBE_COMMAND` - never executed by these tests
## Result
"""


class LayoutTests(unittest.TestCase):
    def setUp(self):
        # Temporary data only; no checkout, worktree, process or real ledger edit.
        gate.RUNTIME.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=gate.RUNTIME)
        self.base = Path(self.temp.name)
        self.tasks = self.base / "tasks"
        self.tasks.mkdir()
        self.task = self.tasks / "T-900-layout-probe.md"
        self.task.write_text(TASK, encoding="utf-8")
        (self.tasks / "_template.md").write_text((FLOW / "tasks" / "_template.md").read_text(encoding="utf-8"), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_storage_and_git_use_distinct_roots(self):
        self.assertEqual(gate.ROOT, REPO)
        self.assertEqual(gate.TASKS, FLOW / "tasks")
        self.assertEqual(ledger.ledger_path(), FLOW / "state" / "tasks.md")
        git_root = Path(gate.git("rev-parse", "--show-toplevel"))
        self.assertEqual(git_root, REPO)
        self.assertEqual(dashboard.ROOT, REPO)

    def test_role_prompt_points_to_relocated_instructions(self):
        with patch.object(gate, "TASKS", self.tasks):
            task = gate.parse("T-900")
        self.assertIn(".agentflow/roles/developer.md", task["prompt"])
        self.assertIn(".agentflow/docs/ai-handoff-protocol.md", task["prompt"])
        self.assertEqual(task["rel"], self.task.relative_to(REPO).as_posix())

    def test_preflight_resolves_product_setup_and_relocated_rules(self):
        original_read = Path.read_text
        rules = FLOW / "docs" / "engineering-rules.md"

        def read(path, *args, **kwargs):
            if path == rules:
                return "## Preflight\n\n- deny: LAYOUT_PROBE_COMMAND\n"
            return original_read(path, *args, **kwargs)

        original_exists = Path.exists

        def exists(path, *args, **kwargs):  # the rules file is simulated, it need not exist on disk
            return True if path == rules else original_exists(path, *args, **kwargs)

        with patch.object(gate, "TASKS", self.tasks), patch.object(gate, "ledger_rows", return_value={}),                 patch.object(Path, "read_text", read), patch.object(Path, "exists", exists):
            _, problems = gate.preflight("T-900", False, set())
        self.assertTrue(any("matches project deny rule" in p for p in problems))
        self.assertFalse(any("Setup" in p for p in problems), problems)

    def test_dashboard_reads_ledger_history_before_relocation(self):
        old_ledger = "| ID | Status | Depends on |\n|---|---|---|\n| T-900 | ready | none |\n"
        commit = "e849d7c"
        with patch.object(dashboard, "git", side_effect=[f"{commit}\t2026-10-06T00:00:00+03:00\tlayout", "", old_ledger]) as git_mock:
            timeline, _, _ = dashboard.history()
        self.assertEqual(timeline["T-900"][0][1], "В очереди")
        self.assertEqual(git_mock.call_args_list[-1].args, ("show", f"{commit}:state/tasks.md"))

    def test_task_history_does_not_double_prefix_new_paths(self):
        output = "@2026-10-06T00:00:00+03:00\n.agentflow/tasks/T-900-probe.md\ntasks/T-901-old.md\n"
        with patch.object(subprocess, "run", return_value=SimpleNamespace(stdout=output)):
            first, _ = dashboard.git_dates()
        self.assertIn(".agentflow/tasks/T-900-probe.md", first)
        self.assertIn(".agentflow/tasks/T-901-old.md", first)
        self.assertFalse(any(".agentflow/.agentflow" in k for k in first))

    def test_dashboard_links_resolve_to_the_actual_task_file(self):
        cells = ["T-900", "Layout probe", "1", "developer", "codex", "ready", "none", "", "", "2026-10-06 00:00"]
        ledger_file = self.base / "ledger.md"
        ledger_file.write_text(ledger.HEAD + ledger.join_row(ledger.COLS) + "\n" + "|---" * len(ledger.COLS) + "|\n" + ledger.join_row(cells) + "\n", encoding="utf-8")
        out = self.base / "out"
        with patch.object(dashboard, "LEDGER", ledger_file), patch.object(dashboard, "TASKS", self.tasks), patch.object(dashboard, "OUT", out), contextlib.redirect_stdout(io.StringIO()):
            dashboard.main()
        page = (out / "index.html").read_text(encoding="utf-8")
        href = json.loads(re.search(r'"fileHref": ("[^"]+")', page).group(1))
        self.assertEqual((out / href).resolve(), self.task.resolve())
        self.assertIn(f'"project": "{REPO.name}"', page)


if __name__ == "__main__":
    unittest.main()
