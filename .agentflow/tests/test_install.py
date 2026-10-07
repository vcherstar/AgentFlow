"""install.py on throwaway repositories: existing project files survive, nothing is deleted, refusals hold."""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
TEMPLATE = FLOW.parent
sys.path.insert(0, str(FLOW / "tools"))
import install  # noqa: E402

OWN_AGENTS = "# Workspace rules\n\nOne task, one branch.\n"
OWN_IGNORE = "/graft/\n"


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "project"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "master")
        sh(self.repo, "git", "config", "user.email", "t@example.invalid")
        sh(self.repo, "git", "config", "user.name", "t")
        # a project that already has its own instructions, tasks/ and tools/ (like a real workspace)
        (self.repo / "AGENTS.md").write_text(OWN_AGENTS, encoding="utf-8")
        (self.repo / ".gitignore").write_text(OWN_IGNORE, encoding="utf-8")
        (self.repo / "tasks").mkdir()
        (self.repo / "tasks" / "plan.md").write_text("own task notes\n", encoding="utf-8")
        (self.repo / "tools").mkdir()
        (self.repo / "tools" / "verify.ps1").write_text("# own script\n", encoding="utf-8")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def run_install(self, update=False, dry=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return install.install(self.repo, update, dry)

    def test_install_keeps_project_files_and_adds_blocks(self):
        plan = self.run_install()
        self.assertTrue(plan.actions)
        agents = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith(OWN_AGENTS.rstrip("\n")), "own rules must stay first and unchanged")
        self.assertIn(install.BEGIN, agents)
        self.assertIn(f"AgentFlow version: {install.version()}", agents)
        self.assertTrue((self.repo / ".gitignore").read_text(encoding="utf-8").startswith("/graft/"))
        self.assertEqual((self.repo / "tasks" / "plan.md").read_text(encoding="utf-8"), "own task notes\n")
        self.assertEqual((self.repo / "tools" / "verify.ps1").read_text(encoding="utf-8"), "# own script\n")
        for rel in ("docs/ai-handoff-protocol.md", "tools/gate.py", "tools/run-task.ps1", "VERSION",
                    "state/handoff.md", "docs/project-rules.md"):
            self.assertTrue((self.repo / ".agentflow" / rel).exists(), rel)
        self.assertFalse((self.repo / ".agentflow" / "state" / "tasks.md").exists(), "ledger is created by ledger.py")
        self.assertFalse(any(p.name == "__pycache__" for p in (self.repo / ".agentflow").rglob("*")))
        self.assertEqual((self.repo / ".claude" / "commands" / "start-role.md").read_text(encoding="utf-8"),
                         install.command_pointer("start-role"))

    def test_dry_run_writes_nothing(self):
        before = sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob("*") if ".git" not in p.parts)
        plan = self.run_install(dry=True)
        after = sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob("*") if ".git" not in p.parts)
        self.assertTrue(plan.actions)
        self.assertEqual(before, after)

    def test_second_install_refused_and_update_is_idempotent(self):
        self.run_install()
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "agentflow")
        with self.assertRaises(SystemExit):
            self.run_install()
        self.assertEqual(self.run_install(update=True).actions, [])

    def test_update_refuses_uncommitted_template_file_and_keeps_project_additions(self):
        self.run_install()
        (self.repo / ".agentflow" / "tools" / "launch.ps1").write_text("# machine wrapper\n", encoding="utf-8")
        (self.repo / ".agentflow" / "state" / "decisions.md").write_text("# Decisions\n\nmine\n", encoding="utf-8")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "agentflow")
        gate = self.repo / ".agentflow" / "tools" / "gate.py"
        gate.write_text(gate.read_text(encoding="utf-8") + "\n# local edit\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as stop:
            self.run_install(update=True)
        self.assertIn(".agentflow/tools/gate.py", str(stop.exception))
        sh(self.repo, "git", "checkout", "--", ".agentflow/tools/gate.py")
        plan = self.run_install(update=True)
        self.assertTrue(any("launch.ps1" in n for n in plan.notes))
        self.assertTrue((self.repo / ".agentflow" / "tools" / "launch.ps1").exists())
        self.assertIn("mine", (self.repo / ".agentflow" / "state" / "decisions.md").read_text(encoding="utf-8"))

    def test_block_is_replaced_not_duplicated(self):
        self.run_install()
        p = self.repo / "AGENTS.md"
        p.write_text(p.read_text(encoding="utf-8").replace("AgentFlow version:", "AgentFlow version: OLD"), encoding="utf-8")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "agentflow")
        self.run_install(update=True)
        text = p.read_text(encoding="utf-8")
        self.assertEqual(text.count(install.BEGIN), 1)
        self.assertNotIn("OLD", text)
        self.assertTrue(text.startswith(OWN_AGENTS.rstrip("\n")))

    def test_foreign_command_file_is_kept(self):
        own = self.repo / ".claude" / "commands" / "start-session.md"
        own.parent.mkdir(parents=True)
        own.write_text("my own command\n", encoding="utf-8")
        plan = self.run_install()
        self.assertEqual(own.read_text(encoding="utf-8"), "my own command\n")
        self.assertTrue(any("start-session.md" in n for n in plan.notes))

    def test_refusals(self):
        plain = Path(self.tmp.name) / "plain"  # not a repository root (possibly inside an unrelated repository)
        plain.mkdir()
        with self.assertRaises(SystemExit):
            install.install(plain, False, True)
        sub = self.repo / "packages" / "web"  # a subfolder of the project repository
        sub.mkdir(parents=True)
        with self.assertRaises(SystemExit) as stop:
            install.install(sub, False, True)
        self.assertIn("--inside-repo", str(stop.exception))
        self.assertTrue(install.install(sub, False, True, inside_repo=True).actions)
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "ai-handoff-protocol.md").write_text("old\n", encoding="utf-8")
        (self.repo / "tools" / "gate.py").write_text("old\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as stop:
            self.run_install()
        self.assertIn("root layout", str(stop.exception))


class TemplateEntryPointTests(unittest.TestCase):
    """The template's own entry points carry exactly what install.py writes into a project."""

    def test_entry_points_match_installer(self):
        self.assertIn(install.agents_block(), (TEMPLATE / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn(install.claude_block(), (TEMPLATE / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertIn(install.gitignore_block(), (TEMPLATE / ".gitignore").read_text(encoding="utf-8"))
        for name in install.COMMANDS:
            self.assertEqual((TEMPLATE / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8"),
                             install.command_pointer(name))
            self.assertTrue((FLOW / "commands" / f"{name}.md").exists())


if __name__ == "__main__":
    unittest.main()
