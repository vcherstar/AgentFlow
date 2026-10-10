"""upstream.py: drift between an installed project and the template, discovered and reported."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

FLOW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLOW / "tools"))
import machine_capacity  # noqa: E402
import upstream  # noqa: E402

TEMPLATE_FILES = {
    ".agentflow/VERSION": "9.9.9\n",
    ".agentflow/README.md": "# .agentflow\n",
    ".agentflow/docs/ai-handoff-protocol.md": "protocol\n",
    ".agentflow/roles/orchestrator.md": "role\n",
    ".agentflow/tools/tick.py": "tick v1\n",
    ".agentflow/tools/install.py": "installer\n",
    "CHANGELOG.md": "# Changelog\n",
}


def make_tree(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"AGENTFLOW_MACHINE_STATE_DIR": str(Path(self.tmp.name) / "machine")})
        env.start()
        self.addCleanup(env.stop)
        self.template = Path(self.tmp.name) / "template"
        self.project = Path(self.tmp.name) / "project"
        make_tree(self.template, TEMPLATE_FILES)
        make_tree(self.project, {k: v for k, v in TEMPLATE_FILES.items() if k != "CHANGELOG.md"})

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(FLOW / "tools" / "upstream.py"), *args],
                              capture_output=True, text=True, cwd=self.project)

    def test_clean_project_reports_no_drift(self):
        rep = upstream.report(self.template, [self.project])
        self.assertFalse(rep["projects"][0]["behind"])
        self.assertFalse(upstream.drifted(rep["projects"][0]))
        r = self.run_cli("--check", "--template", str(self.template), "--project", str(self.project))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("clean: no drift", r.stdout)

    def test_differs_missing_and_added_are_listed(self):
        (self.project / ".agentflow" / "tools" / "tick.py").write_text("tick v2 local\n", encoding="utf-8")
        (self.project / ".agentflow" / "docs" / "ai-handoff-protocol.md").unlink()
        (self.project / ".agentflow" / "tools" / "mine.py").write_text("project tool\n", encoding="utf-8")
        rep = upstream.report(self.template, [self.project])
        e = rep["projects"][0]
        self.assertEqual(e["differs"], ["tools/tick.py"])
        self.assertEqual(e["missing"], ["docs/ai-handoff-protocol.md"])
        self.assertEqual(e["added"], ["tools/mine.py"])
        r = self.run_cli("--check", "--template", str(self.template), "--project", str(self.project))
        self.assertEqual(r.returncode, 1)
        self.assertIn("same version: review for upstream merge", r.stdout)
        self.assertIn("port upstream or keep project-owned", r.stdout)

    def test_older_version_marks_the_project_behind(self):
        (self.project / ".agentflow" / "VERSION").write_text("9.9.8\n", encoding="utf-8")
        rep = upstream.report(self.template, [self.project])
        self.assertTrue(rep["projects"][0]["behind"])
        r = self.run_cli("--template", str(self.template), "--project", str(self.project))
        self.assertIn("install.py --update", r.stdout)

    def test_line_endings_are_not_drift(self):
        (self.project / ".agentflow" / "tools" / "tick.py").write_bytes(b"tick v1\r\n")
        rep = upstream.report(self.template, [self.project])
        self.assertFalse(upstream.drifted(rep["projects"][0]))

    def test_project_owned_files_are_never_compared(self):
        (self.project / ".agentflow" / "state").mkdir(exist_ok=True)
        (self.project / ".agentflow" / "state" / "tasks.md").write_text("ledger\n", encoding="utf-8")
        (self.project / ".agentflow" / "docs" / "project-rules.md").write_text("rules\n", encoding="utf-8")
        rep = upstream.report(self.template, [self.project])
        self.assertFalse(upstream.drifted(rep["projects"][0]))

    def test_template_source_points_back(self):
        make_tree(self.project, {".agentflow/template-source.json":
                                 json.dumps({"template": str(self.template)})})
        self.assertEqual(upstream.find_template(None, [], cwd=self.project), self.template.resolve())
        r = self.run_cli("--check", "--project", str(self.project))   # no --template: source file finds it
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_template_checkout_is_recognized_by_changelog(self):
        self.assertTrue(upstream.looks_like_template(self.template))
        self.assertFalse(upstream.looks_like_template(self.project))

    def test_known_projects_come_from_the_machine_registry(self):
        other = Path(self.tmp.name) / "other"
        make_tree(other, {".agentflow/VERSION": "9.9.9\n"})
        token, reason = machine_capacity.claim("claude", other, "T-001", pid=os.getpid())
        self.assertTrue(token, reason)
        projects = upstream.find_projects([], self.template.resolve(), cwd=self.template)
        self.assertIn(other.resolve(), projects)
        self.assertNotIn(self.template.resolve(), projects)

    def test_json_output(self):
        r = self.run_cli("--json", "--template", str(self.template), "--project", str(self.project))
        rep = json.loads(r.stdout)
        self.assertEqual(rep["version"], "9.9.9")
        self.assertEqual(len(rep["projects"]), 1)


if __name__ == "__main__":
    unittest.main()
