"""End to end on throwaway git repositories: a developer task in one repository and in a two-repository workspace,
from worktrees to accept.py. The real tools run as subprocesses from a copy of .agentflow/, so no mock hides a path."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
PY = sys.executable


def sh(cwd, *cmd, check=True):
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise AssertionError(f"{' '.join(map(str, cmd))} failed in {cwd}:\n{p.stdout}\n{p.stderr}")
    return p


def new_repo(path, file="README.md"):
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "git", "init", "-q", "-b", "master")
    sh(path, "git", "config", "user.email", "t@example.invalid")
    sh(path, "git", "config", "user.name", "t")
    (path / file).write_text("base\n", encoding="utf-8")
    sh(path, "git", "add", "-A")
    sh(path, "git", "commit", "-qm", "init")


def task_text(repo_line, allowed, worktree):
    return f"""# T-001: Probe

Role: developer
Tool: codex
Stage: 1
Depends on: none
{repo_line}Branch: t-001-probe
Worktree: {worktree}
Independent check: none - end-to-end probe
Resume: none

## Goal

Probe.

## Allowed files

{allowed}

## Do not touch

- `.agentflow/`

## Setup

nothing

## Port

nothing

## Rebuild together

nothing

## Acceptance criteria

- [ ] The probe file exists.

## Checks

- `git --version` - git runs

## Result
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        sh(self.base, "git", "--version")  # keep Windows handles calm before cleanup
        self.tmp.cleanup()

    def install_tools(self, root):
        flow = root / ".agentflow"
        shutil.copytree(FLOW / "tools", flow / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        (flow / "tasks").mkdir(parents=True)
        shutil.copy(FLOW / "tasks" / "_template.md", flow / "tasks" / "_template.md")
        (flow / "state").mkdir()
        return flow

    def tool(self, root, name, *args, check=True):
        return sh(root, PY, "-X", "utf8", root / ".agentflow" / "tools" / name, *args, check=check)

    def start_attempt(self, root):
        """What run-task.ps1 records and creates: the attempt baseline and the worktrees from gate.py 'checkouts'."""
        out = root / ".agentflow" / "task.json"
        self.tool(root, "gate.py", "task", "T-001", "--out", out)
        t = json.loads(out.read_text(encoding="utf-8"))
        out.unlink()
        rt = root / ".agentflow" / "tasks" / ".runtime"
        rt.mkdir(parents=True, exist_ok=True)
        (rt / "T-001.json").write_text(json.dumps({"taskId": "T-001", "attempts": [
            {"n": 1, "status": "exited", "exitCode": 0, "baseline": t["baseline"]}]}), encoding="utf-8")
        for c in t["checkouts"]:
            Path(c["path"]).parent.mkdir(parents=True, exist_ok=True)
            sh(c["repo"], "git", "worktree", "add", c["path"], "-b", c["branch"])
        return t

    def commit_in(self, path, rel):
        f = Path(path) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("probe\n", encoding="utf-8")
        sh(path, "git", "add", "-A")
        sh(path, "git", "commit", "-qm", "[T-001] feat: probe")
        return sh(path, "git", "rev-parse", "HEAD").stdout.strip()

    def finish(self, root, change):
        p = root / ".agentflow" / "tasks" / "T-001-probe.md"
        p.write_text(p.read_text(encoding="utf-8") + f"Outcome: completed\nChange: {change}\n", encoding="utf-8")
        self.tool(root, "ledger.py", "add", "T-001", "--title", "Probe", "--stage", "1", "--role", "developer", "--tool", "codex")
        self.tool(root, "ledger.py", "set", "T-001", "--status", "in progress")


class SingleRepositoryTests(Base):
    def test_develop_and_accept(self):
        root = self.base / "project"
        new_repo(root)
        self.install_tools(root)
        wt = self.base / "wt" / "project-t-001-probe"
        (root / ".agentflow" / "tasks" / "T-001-probe.md").write_text(task_text("", "- `src/`", wt), encoding="utf-8")
        t = self.start_attempt(root)
        self.assertEqual(t["repos"], [""])
        sha = self.commit_in(wt, "src/probe.txt")
        self.finish(root, sha)
        r = self.tool(root, "accept.py", "T-001", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((root / "src" / "probe.txt").exists())
        self.assertFalse(wt.exists())
        self.assertEqual(sh(root, "git", "branch", "--list", "t-001-probe").stdout.strip(), "")
        self.assertIn(sha, (root / ".agentflow" / "state" / "tasks.md").read_text(encoding="utf-8"))


    def test_tester_task_json_reads_in_powershell(self):
        """Regression: the single repository's commit key "" broke ConvertFrom-Json in run-task.ps1 for testers."""
        root = self.base / "project"
        new_repo(root)
        self.install_tools(root)
        wt = self.base / "wt" / "project-t-001-probe"
        (root / ".agentflow" / "tasks" / "T-001-probe.md").write_text(task_text("", "- `src/`", wt), encoding="utf-8")
        self.start_attempt(root)
        sha = self.commit_in(wt, "src/probe.txt")
        (root / ".agentflow" / "tasks" / "T-002-test.md").write_text(
            f"# T-002: Test\n\nRole: tester\nTool: codex\nStage: 1\nDepends on: none\nVerifies: T-001 @ {sha}\n"
            "Resume: none\n\n## Result\n", encoding="utf-8")
        out = root / ".agentflow" / "t2.json"
        self.tool(root, "gate.py", "task", "T-002", "--out", out)
        data = json.loads(out.read_text(encoding="utf-8"), object_pairs_hook=lambda pairs: (
            self.assertNotIn("", [k for k, _ in pairs]) or dict(pairs)))
        self.assertEqual(data["sha"], sha)
        pwsh = shutil.which("pwsh")
        if pwsh:
            r = sh(root, pwsh, "-NoProfile", "-Command", f"(Get-Content '{out}' -Raw | ConvertFrom-Json).sha", check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), sha)


class WorkspaceTests(Base):
    def make_workspace(self, allowed="- `api/src/`\n- `web/src/`"):
        ws = self.base / "workspace"
        new_repo(ws / "api")
        new_repo(ws / "web")
        (ws / ".gitignore").write_text("/api/\n/web/\n", encoding="utf-8")  # before the first commit, as install does
        new_repo(ws, file="AGENTS.md")  # the memory repository; nested repositories are ignored by it
        self.assertEqual(sh(ws, "git", "ls-files").stdout.split(), [".gitignore", "AGENTS.md"])
        flow = self.install_tools(ws)
        (flow / "workspace.json").write_text(json.dumps({"repos": ["api", "web"]}), encoding="utf-8")
        wt = self.base / "wt" / "workspace-t-001-probe"
        (flow / "tasks" / "T-001-probe.md").write_text(
            task_text("Repo: api, web\n", allowed, wt), encoding="utf-8")
        return ws, wt

    def test_two_repositories_develop_and_accept(self):
        ws, wt = self.make_workspace()
        t = self.start_attempt(ws)
        self.assertEqual(t["repos"], ["api", "web"])
        self.assertEqual([Path(c["path"]) for c in t["checkouts"]], [wt / "api", wt / "web"])
        a = self.commit_in(wt / "api", "src/a.txt")
        w = self.commit_in(wt / "web", "src/w.txt")
        self.finish(ws, f"api@{a}, web@{w}")
        r = self.tool(ws, "accept.py", "T-001", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((ws / "api" / "src" / "a.txt").exists())
        self.assertTrue((ws / "web" / "src" / "w.txt").exists())
        self.assertFalse(wt.exists(), "worktrees and their folder are removed")
        for repo in ("api", "web"):
            self.assertEqual(sh(ws / repo, "git", "branch", "--list", "t-001-probe").stdout.strip(), "")
        self.assertIn(f"api@{a}, web@{w}", (ws / ".agentflow" / "state" / "tasks.md").read_text(encoding="utf-8"))

    def test_file_outside_allowed_fails_verify(self):
        ws, wt = self.make_workspace()
        self.start_attempt(ws)
        a = self.commit_in(wt / "api", "docs/outside.txt")
        w = self.commit_in(wt / "web", "src/w.txt")
        self.finish(ws, f"api@{a}, web@{w}")
        r = self.tool(ws, "gate.py", "verify", "T-001", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("api/docs/outside.txt", r.stdout)

    def test_conflict_in_one_repository_merges_none(self):
        ws, wt = self.make_workspace(allowed="- `api/src/`\n- `web/`")
        self.start_attempt(ws)
        a = self.commit_in(wt / "api", "src/a.txt")
        w = self.commit_in(wt / "web", "README.md")  # the branch rewrites README.md ...
        (ws / "web" / "README.md").write_text("changed on master\n", encoding="utf-8")  # ... and so does master
        sh(ws / "web", "git", "commit", "-qam", "master change")
        self.finish(ws, f"api@{a}, web@{w}")
        api_head = sh(ws / "api", "git", "rev-parse", "HEAD").stdout.strip()
        r = self.tool(ws, "accept.py", "T-001", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("merge-tree", r.stdout, "verify passed; the trial merge must be what stopped it")
        self.assertNotIn("3. merge", r.stdout)
        self.assertEqual(sh(ws / "api", "git", "rev-parse", "HEAD").stdout.strip(), api_head, "api must not be merged")

    def test_tester_and_repo_validation(self):
        ws, wt = self.make_workspace()
        self.start_attempt(ws)
        a = self.commit_in(wt / "api", "src/a.txt")
        w = self.commit_in(wt / "web", "src/w.txt")
        tester = ws / ".agentflow" / "tasks" / "T-002-test.md"
        tester.write_text(f"# T-002: Test\n\nRole: tester\nTool: codex\nStage: 1\nDepends on: none\n"
                          f"Verifies: T-001 @ web@{w}, api@{a}\nResume: none\n\n## Result\n", encoding="utf-8")
        out = ws / ".agentflow" / "t2.json"
        self.tool(ws, "gate.py", "task", "T-002", "--out", out)
        t = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(t["repos"], ["api", "web"])
        self.assertEqual(t["sha"], f"api@{a}, web@{w}")
        self.assertEqual([c["sha"] for c in t["checkouts"]], [a, w])
        bad = ws / ".agentflow" / "tasks" / "T-003-bad.md"
        bad.write_text(task_text("Repo: mobile\n", "- `mobile/`", self.base / "x").replace("T-001", "T-003")
                       .replace("t-001-probe", "t-003-bad"), encoding="utf-8")
        r = self.tool(ws, "gate.py", "task", "T-003", "--out", out, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not in .agentflow/workspace.json", r.stderr)


if __name__ == "__main__":
    unittest.main()
