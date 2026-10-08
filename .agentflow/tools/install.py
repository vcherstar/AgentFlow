"""Install AgentFlow into a project, or update it, without touching anything outside its own files.

Run from the template checkout:

  python <template>/.agentflow/tools/install.py <project>             # install
  python <template>/.agentflow/tools/install.py <project> --update    # replace template-owned files with this version
  add --dry-run to print every action without writing anything
  add --inside-repo to install into a subfolder of a repository (a monorepo package) on purpose

A folder that holds several repositories (a workspace):

  python <template>/.agentflow/tools/install.py <workspace> --workspace [--repos a,b] [--git-init]

  The workspace root becomes a small memory repository that tracks only AgentFlow files and the root entry points;
  the product repositories stay independent and are listed in `.agentflow/workspace.json` (found automatically:
  child folders with their own `.git`). `--git-init` runs `git init` in the workspace root when it is not a
  repository yet; without it the installer refuses and says so.

What it writes (protocol section "Installing or updating AgentFlow"):
- `<project>/.agentflow/`: the template-owned files (protocol, roles, commands, Task File template, tools, dashboard,
  tests, README, VERSION). Project-owned memory files are created only when missing and never overwritten.
- `AGENTS.md`, `CLAUDE.md`, `.gitignore`: one block between `agentflow:begin` / `agentflow:end` markers; the rest of
  an existing file is left exactly as it is. A missing file is created.
- `.claude/commands/<name>.md`: one-line pointers to `.agentflow/commands/`; an existing file with other content is
  kept and reported.

Safety: it deletes nothing; it refuses a target that is not the root of a git work tree (the tools need git, and a
folder that merely sits inside a larger repository, such as a home folder under git, is almost always a mistake);
it refuses to
install over an existing `.agentflow/` without --update; --update refuses while a template-owned file it would
replace has uncommitted changes. Files the project added inside `.agentflow/` are kept and listed.
"""
import argparse
import filecmp
import json
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATE_FLOW = Path(__file__).resolve().parent.parent  # <template>/.agentflow

# template-owned paths inside .agentflow/ (directories are copied recursively, minus SKIP)
OWNED = ["README.md", "VERSION", "docs/ai-handoff-protocol.md", "docs/typesafe.md", "roles", "commands", "tasks/_template.md",
         "tools", "dashboard", "tests"]
SKIP = {"__pycache__", "out", "versions", ".runtime"}

# project-owned files created once from a stub, never overwritten
STUBS = {
    "state/handoff.md": "# Handoff\n\nAs of: <date>, <main>@<SHA>\n\n## Goal\n\n## Verified state\n\n## Read first\n",
    "state/current-step.md": "# Current step\n\nInstall AgentFlow: fill Project rules, then the plan (protocol, \"Installing or updating AgentFlow\").\n",
    "state/decisions.md": "# Decisions\n",
    "state/known-issues.md": "# Known issues\n",
    "state/session-log.md": "# Session log\n",
    "state/questions.md": "# Questions for the human\n\nWritten by a background Orchestrator (protocol, \"Autonomous orchestration\"); answered entries are removed.\n",
    "docs/project-plan.md": "# Roadmap\n\n## Stage 1: <name>\n\nState: current\n\nExit criteria:\n- <checkable criterion>\n",
    "docs/project-rules.md": (
        "# Project rules\n\nProject-owned; a template update never changes this file "
        "(protocol, Terms: Project rules).\n\n"
        "- Main branch: <main | master>.\n"
        "- `<worktrees>` = <one folder outside the repository and outside cloud sync>.\n"
        "- Talk to the human in <language>.\n\n"
        "## Preflight\n\n- deny: `\\bgit\\s+push\\b`\n- parallel: codex=1\n\n"
        "## Tool routing\n\n<installed tools, their paths and limits on this machine>\n"),
    "docs/model-options.json": (
        '{\n  "policy": "<how to choose: what is free or has unused capacity, what is limited, testers on another tool>",\n'
        '  "jev_model": "jev-1.13.0",\n'
        '  "options": {\n'
        '    "claude": {"tool": "claude", "what": "complex multi-file work and architecture", "not_for": "long mechanical work"},\n'
        '    "codex": {"tool": "codex", "what": "long tasks, testers (sandboxed review)", "not_for": "UI design"}\n'
        '  }\n}\n'),
}

COMMANDS = ["handoff-cmd", "start-role", "start-session", "update-memory", "update-runbook"]
GITIGNORE = [".agentflow/tasks/.runtime/", ".agentflow/dashboard/out/", ".agentflow/dashboard/versions/", "__pycache__/"]
NOT_REPOS = {".git", ".agentflow", ".claude", "node_modules", ".worktrees"}
BEGIN, END = "<!-- agentflow:begin -->", "<!-- agentflow:end -->"
GBEGIN, GEND = "# agentflow:begin", "# agentflow:end"


def version():
    return (TEMPLATE_FLOW / "VERSION").read_text(encoding="utf-8").strip()


def agents_block():
    return f"""{BEGIN}
## AgentFlow

AgentFlow version: {version()}. Workflow files live in `.agentflow/`; this block is template-owned and replaced on update.

Source of truth: `.agentflow/docs/ai-handoff-protocol.md`. Read it first, then follow it. Project rules:
`.agentflow/docs/project-rules.md` (and any rules in this file outside this block).

- With a role (`.agentflow/roles/<role>.md`, or `/start-role <role> ...`): protocol section "Starting a role session".
  Workers (developer, tester, deployer) do not update project memory.
- Without a role (Single Mode): protocol section "Starting a new AI session" before substantial work; "Updating memory"
  before ending a long session.
{END}"""


def claude_block():
    return f"""{BEGIN}
## AgentFlow

@AGENTS.md

Slash commands (plain Markdown in `.agentflow/commands/`, pointers in `.claude/commands/`): `/start-role <role> [task file]`,
`/start-session`, `/update-memory`, `/handoff-cmd`, `/update-runbook`.
{END}"""


def command_pointer(name):
    return f"Read `.agentflow/commands/{name}.md` and follow its instructions.\nArguments: $ARGUMENTS\n"


WORKSPACE_ONLY = ["/*", "!/.agentflow/", "!/AGENTS.md", "!/CLAUDE.md", "!/.claude/", "!/.gitignore",
                  "/.claude/settings.local.json"]


def gitignore_block(repos=()):
    """Workspace: the memory repository tracks only AgentFlow files and the root entry points (track more with a
    `!/<path>` line outside the block) and names every product repository it holds as ignored."""
    ws = WORKSPACE_ONLY + [f"/{r}/" for r in repos] if repos else []
    return "\n".join([GBEGIN] + ws + GITIGNORE + [GEND])


def nested_repos(target: Path):
    return sorted(d.name for d in target.iterdir()
                  if d.is_dir() and d.name not in NOT_REPOS and (d / ".git").exists())


class Plan:
    def __init__(self, dry):
        self.dry, self.actions, self.notes = dry, [], []

    def write(self, path: Path, text: str, what: str):
        self.actions.append(f"{what}: {path}")
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")

    def copy(self, src: Path, dst: Path, what: str):
        self.actions.append(f"{what}: {dst}")
        if not self.dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def git(target, *args):
    p = subprocess.run(["git", "-C", str(target), *args], capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def owned_files():
    """(relative path inside .agentflow, source path) for every template-owned file."""
    out = []
    for rel in OWNED:
        src = TEMPLATE_FLOW / rel
        if src.is_dir():
            for f in sorted(src.rglob("*")):
                if f.is_file() and not (SKIP & set(f.relative_to(TEMPLATE_FLOW).parts)):
                    out.append((f.relative_to(TEMPLATE_FLOW).as_posix(), f))
        elif src.exists():
            out.append((rel, src))
    return out


def merge_block(existing: str | None, block: str, begin: str, end: str, new_file_head: str) -> str:
    if existing is None:
        return new_file_head + block + "\n"
    if begin in existing and end in existing:
        a, rest = existing.split(begin, 1)
        _, b = rest.split(end, 1)
        return a + block + b
    return existing.rstrip("\n") + "\n\n" + block + "\n"


def install(target: Path, update: bool, dry: bool, inside_repo: bool = False,
            workspace: bool = False, repos=None, git_init: bool = False) -> Plan:
    plan = Plan(dry)
    target = target.resolve()
    if not target.is_dir():
        raise SystemExit(f"STOP: {target} is not a folder")
    if target == TEMPLATE_FLOW.parent:
        raise SystemExit("STOP: the target is the template itself")
    ws_file = target / ".agentflow" / "workspace.json"
    if update and ws_file.exists():
        workspace = True
        repos = repos or json.loads(ws_file.read_text(encoding="utf-8-sig")).get("repos", [])
    if workspace:
        repos = [r.strip().strip("/\\") for r in (repos or nested_repos(target)) if r.strip()]
        if not repos:
            raise SystemExit(f"STOP: no repositories found in {target} (child folders with .git); pass --repos a,b")
        missing = [r for r in repos if not (target / r / ".git").exists()]
        if missing:
            raise SystemExit(f"STOP: not git repositories: {', '.join(missing)}")
    code, top = git(target, "rev-parse", "--show-toplevel")
    if workspace and (code or Path(top).resolve() != target):
        if not git_init:
            raise SystemExit(f"STOP: the workspace root {target} is not a git repository of its own. AgentFlow keeps the "
                             "workspace memory in a small repository there that tracks only AgentFlow files; run again "
                             "with --git-init to create it (the product repositories stay untouched).")
        plan.actions.append(f"git init (memory repository): {target}")
        if not dry:
            subprocess.run(["git", "init", "-q", "-b", "main", str(target)], check=True)
        code, top = 0, str(target)
    if code and dry and git_init and workspace:
        code, top = 0, str(target)
    if code:
        nested = nested_repos(target)
        raise SystemExit(f"STOP: {target} is not a git work tree. AgentFlow tools need git (branches, worktrees, "
                         "verify). Install into the root of a git repository"
                         + (f"; this folder holds repositories ({', '.join(nested)}): install it as a workspace with "
                            "--workspace --git-init" if nested else "") + ".")
    if Path(top).resolve() != target:
        if not inside_repo:
            nested = nested_repos(target)
            raise SystemExit(f"STOP: {target} is not the root of its git repository ({top}). Install into the "
                             "repository root, or pass --inside-repo if this subfolder is meant to hold AgentFlow"
                             + (f"; this folder holds repositories ({', '.join(nested)}): install it as a workspace "
                                "with --workspace --git-init" if nested else "") + ".")
        plan.notes.append(f"note: {target} is inside the repository {top}; .agentflow/ will be created here, "
                          "and git commands run against that repository")
    if (target / "tools" / "gate.py").exists() and (target / "docs" / "ai-handoff-protocol.md").exists():
        raise SystemExit("STOP: an AgentFlow 2.1 root layout (tools/, docs/ at the project root) is installed. "
                         "Move it into .agentflow/ first (GUIDE.md, \"Migration from the root layout\").")

    flow = target / ".agentflow"
    installed = (flow / "docs" / "ai-handoff-protocol.md").exists()
    if installed and not update:
        raise SystemExit(f"STOP: {flow} already holds AgentFlow; run again with --update")
    if update and not installed:
        raise SystemExit(f"STOP: {flow} has no AgentFlow to update; run without --update")

    files = owned_files()
    if update:
        changed = []
        for rel, _ in files:
            p = f".agentflow/{rel}"
            if (flow / rel).exists():
                _, st = git(target, "status", "--porcelain", "--", p)
                if st:
                    changed.append(p)
        if changed:
            raise SystemExit("STOP: template-owned files have uncommitted changes; commit or move them to Project "
                             "rules first:\n  " + "\n  ".join(changed))
        tpl = {rel for rel, _ in files}
        for d in ("roles", "commands", "tools", "dashboard", "tests"):
            for f in sorted((flow / d).rglob("*")) if (flow / d).exists() else []:
                rel = f.relative_to(flow).as_posix()
                if f.is_file() and rel not in tpl and not (SKIP & set(f.relative_to(flow).parts)):
                    plan.notes.append(f"kept (project-added, not in the template): .agentflow/{rel}")

    for rel, src in files:
        dst = flow / rel
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            continue
        plan.copy(src, dst, "update" if dst.exists() else "add")

    for rel, text in STUBS.items():
        dst = flow / rel
        if not dst.exists():
            plan.write(dst, text, "create (project-owned stub)")

    if workspace:
        if not ws_file.exists():
            plan.write(ws_file, json.dumps({"repos": repos}, indent=2) + "\n", "create (project-owned workspace list)")
        for r in repos:
            if not dry and (target / ".git").exists():
                _, staged = git(target, "ls-files", "-s", "--", r)
                if staged.startswith("160000"):
                    plan.notes.append(f"note: the memory repository tracks {r} as a submodule link; untrack it with "
                                      f"`git rm --cached {r}` (the repository itself is not touched)")

    for name, block, head in (("AGENTS.md", agents_block(), "# Agent instructions\n\n"),
                              ("CLAUDE.md", claude_block(), "# Claude instructions\n\n")):
        p = target / name
        old = p.read_text(encoding="utf-8") if p.exists() else None
        new = merge_block(old, block, BEGIN, END, head)
        if new != old:
            plan.write(p, new, "create" if old is None else "add/replace AgentFlow block in")

    gi = target / ".gitignore"
    old = gi.read_text(encoding="utf-8") if gi.exists() else None
    new = merge_block(old, gitignore_block(repos if workspace else ()), GBEGIN, GEND, "")
    if new != old:
        plan.write(gi, new, "create" if old is None else "add/replace AgentFlow block in")

    for name in COMMANDS:
        p = target / ".claude" / "commands" / f"{name}.md"
        want = command_pointer(name)
        if not p.exists():
            plan.write(p, want, "create")
        elif p.read_text(encoding="utf-8") != want:
            text = p.read_text(encoding="utf-8")
            if "ai-handoff-protocol.md" in text or ".agentflow/commands/" in text:
                plan.write(p, want, "replace AgentFlow pointer")
            else:
                plan.notes.append(f"kept (not an AgentFlow file): {p}")
    return plan


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", type=Path)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inside-repo", action="store_true")
    ap.add_argument("--workspace", action="store_true", help="the target holds several git repositories")
    ap.add_argument("--repos", help="workspace repositories, comma-separated (default: child folders with .git)")
    ap.add_argument("--git-init", action="store_true", help="create the workspace memory repository")
    a = ap.parse_args()
    plan = install(a.target, a.update, a.dry_run, a.inside_repo, a.workspace,
                   a.repos.split(",") if a.repos else None, a.git_init)
    head = "Would do" if a.dry_run else "Done"
    print(f"AgentFlow {version()} -> {a.target.resolve()}{' (dry run, nothing written)' if a.dry_run else ''}")
    print(f"{head}: {len(plan.actions)} action(s)")
    for x in plan.actions:
        print("  " + x)
    for n in plan.notes:
        print("  " + n)
    if not a.dry_run and plan.actions:
        print("Next: review `git status`, fill .agentflow/docs/project-rules.md and the plan, then commit "
              "(protocol, \"Installing or updating AgentFlow\").")


if __name__ == "__main__":
    main()
