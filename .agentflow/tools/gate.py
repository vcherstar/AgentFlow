#!/usr/bin/env python3
"""Task gates: Task File parsing, launch preflight, acceptance verify, Stage check.

  python .agentflow/tools/gate.py verify T-007      acceptance evidence for one task; appends to .agentflow/tasks/.runtime/T-007.verify.json
  python .agentflow/tools/gate.py stage 2           every Stage 2 task done and its Checks pass on the main branch

Used by .agentflow/tools/run-task.ps1 (pure logic here, side effects there):
  python .agentflow/tools/gate.py task T-007 --out f.json
  python .agentflow/tools/gate.py preflight T-007 [--manual] [--live T-1,T-2] [--tool codex] --out f.json
  python .agentflow/tools/gate.py endcheck T-007 --out f.json

Rules: .agentflow/docs/ai-handoff-protocol.md. Exit code: 0 pass, 1 fail, 2 gate error.

Single repository: the repository holding .agentflow/ is the product repository. Workspace: .agentflow/workspace.json
lists product repositories (folders under the workspace root, which is a small memory repository); a Task File names
its repositories in `Repo:`, and commits are written `<repo>@<sha>, ...`.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402
from paths import FLOW_ROOT, REPO_ROOT, WORKSPACE_FILE  # noqa: E402

ROOT = REPO_ROOT
TASKS = FLOW_ROOT / "tasks"
RUNTIME = TASKS / ".runtime"
SHA = r"[0-9a-fA-F]{7,40}"
VERDICTS = ["pass", "partial", "unverified", "fail"]  # worst last


class TaskError(Exception):
    pass


# --- Task File parsing. HTML comments (template hints) are not content.
def section(text, name):
    m = re.search(rf"(?ms)^## {re.escape(name)}[ \t]*\r?\n(.*?)(?=^## |\Z)", text)
    return re.sub(r"(?s)<!--.*?-->", "", m.group(1)) if m else ""


def field(text, name):
    m = re.search(rf"(?m)^{re.escape(name)}[ \t]*:[ \t]*(.+?)[ \t]*(<!--.*)?$", text)
    return m.group(1).strip() if m else None


def header(text):  # everything above "## Result": the worker must not change it
    # Trailing blank lines do not count: a worker that adds the missing "## Result" heading after an empty line
    # changes nothing above it.
    return re.sub(r"(?ms)^## Result[ \t]*$.*\Z", "", text.replace("\r\n", "\n")).rstrip() + "\n"


def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def bullets(body):
    return [m.group(1) for m in re.finditer(r"(?m)^[ \t]*-[ \t]+(.+?)[ \t]*$", body)]


def paths(body):  # first token of each bullet that looks like a path: "- `src/a.ts` - why"
    out = []
    for b in bullets(body):
        m = re.match(r"^`([^`]+)`", b)
        tok = m.group(1) if m else b.split()[0]
        if re.search(r"[\\/.*]", tok):
            out.append(re.sub(r"^\./", "", tok.replace("\\", "/")).rstrip("/").lower())
    return out


def commands(body):
    return [m.group(1) for b in bullets(body) if (m := re.search(r"`([^`]+)`", b))]


def glob_match(pattern, path):  # * and ** both match across '/': conservative
    if not re.search(r"[*?]", pattern):
        return False
    rx = "^" + re.sub(r"(\\\*)+", ".*", re.escape(pattern)).replace(r"\?", ".") + "(/.*)?$"
    return re.match(rx, path) is not None


def overlap(a, b):  # same file, one folder contains the other, or a glob matches
    return a == b or a.startswith(b + "/") or b.startswith(a + "/") or glob_match(a, b) or glob_match(b, a)


def task_file(tid):
    hits = sorted(TASKS.glob(f"{tid}-*.md"))
    if not hits:
        raise TaskError(f"Task File .agentflow/tasks/{tid}-*.md not found")
    return hits[0]


def git(*args, cwd=ROOT):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    return r.stdout.strip() if r.returncode == 0 else None


def git_ok(*args, cwd=ROOT):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True).returncode == 0


def main_branch(repo=""):
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_dir(repo)) or "main"


# --- repositories: "" is the single repository at ROOT; in a workspace each key is a folder under ROOT
def workspace_repos():
    if not WORKSPACE_FILE.exists():
        return []
    data = json.loads(WORKSPACE_FILE.read_text(encoding="utf-8-sig"))
    return [r.replace("\\", "/").strip("/") for r in data.get("repos", [])]


def repo_dir(repo):
    return ROOT / repo if repo else ROOT


def task_repos(head, role, checked_repos=None):
    ws = workspace_repos()
    raw = field(head, "Repo")
    if not ws:
        if raw:
            raise TaskError("Repo: is for a workspace; this project has no .agentflow/workspace.json")
        return [""]
    if not raw:
        if checked_repos:  # a tester inherits the repositories of the task it checks
            return checked_repos
        if role == "deployer":
            return []
        raise TaskError(f"Repo: is required in a workspace (one or more of: {', '.join(ws)})")
    repos = [r.strip().replace("\\", "/").strip("/") for r in raw.split(",") if r.strip()]
    unknown = [r for r in repos if r not in ws]
    if unknown:
        raise TaskError(f"Repo {', '.join(unknown)} is not in .agentflow/workspace.json ({', '.join(ws)})")
    return repos


def parse_shas(value, repos):
    """`<sha>` (one repository) or `<repo>@<sha>, ...` -> {repo: sha}; None when it does not name every repository."""
    value = (value or "").strip()
    if m := re.match(rf"^({SHA})\b", value):
        return {repos[0]: m.group(1).lower()} if len(repos) == 1 else None
    pairs = dict((k.strip().replace("\\", "/").strip("/"), v.lower())
                 for k, v in re.findall(rf"([^\s,@]+)\s*@\s*({SHA})", value))
    return pairs if pairs and sorted(pairs) == sorted(repos) else None


def sha_text(shas, short=False):
    """{repo: sha} -> the form the ledger, Verifies and records use."""
    cut = (lambda v: v[:7]) if short else (lambda v: v)
    if list(shas) == [""]:
        return cut(shas[""])
    return ", ".join(f"{k}@{cut(v)}" for k, v in sorted(shas.items()))


def sub_path(base, repo):
    return str(Path(base) / repo) if repo else str(base)


def parse_model(value):
    """`Model: <model>[, effort=<level>]` -> (model, effort); either may be None."""
    if not value or "<" in value:  # empty, or the template placeholder left in place: the tool's default model
        return None, None
    parts = [x.strip() for x in value.split(",") if x.strip()]
    model = next((x for x in parts if "=" not in x), None)
    effort = next((x.split("=", 1)[1].strip() for x in parts if x.replace(" ", "").startswith("effort=")), None)
    return model, effort


def checkouts(t):
    """What run-task.ps1 creates per repository: developer worktrees on Branch, tester detached checkouts."""
    if t["role"] == "developer":
        return [{"repo": str(repo_dir(r)), "path": sub_path(t["worktree"], r), "branch": t["branch"]} for r in t["repos"]]
    if t["role"] == "tester":
        return [{"repo": str(repo_dir(r)), "path": sub_path(t["workdir"], r), "sha": t["shas"][r]} for r in t["repos"]]
    return []


def parse(tid):
    """Fields of one Task File. Raises TaskError when a role-required field is missing or malformed."""
    f = task_file(tid)
    text = f.read_text(encoding="utf-8-sig")
    head = header(text)  # fields come from the header only: a worker report may contain "Target:" etc.
    t = {"id": tid, "file": str(f), "rel": f.relative_to(ROOT).as_posix(), "role": field(head, "Role"),
         "branch": field(head, "Branch"), "worktree": field(head, "Worktree"),
         "depends": re.findall(r"T-\d+", field(head, "Depends on") or ""),
         "allowed": paths(section(text, "Allowed files")), "denied": paths(section(text, "Do not touch")),
         "rebuild": [w for b in bullets(section(text, "Rebuild together")) if re.search(r"[a-z0-9]", w := b.replace("`", "").split()[0].lower())],
         "checks": commands(section(text, "Checks")), "acceptance": bullets(section(text, "Acceptance criteria")),
         "independent": field(head, "Independent check"), "target": "local", "env": {}, "setup": [],
         "headerHash": sha256(header(text)), "result": section(text, "Result")}
    t["prompt"] = (f"Your role: .agentflow/roles/{t['role']}.md. Your task: {f}. "
                   "Follow .agentflow/docs/ai-handoff-protocol.md, section 'Starting a role session'. "
                   f"Write your Result into exactly this file ({f}), not into a copy inside your worktree, and do not commit it.")
    setup = section(text, "Setup") + "\n" + section(text, "Port")
    for m in re.finditer(r"(?m)^[ \t]*-[ \t]*(link|copy|env)[ \t]*:[ \t]*(.+?)[ \t]*$", setup):
        if m.group(1) == "env":
            k, _, v = m.group(2).partition("=")
            t["env"][k.strip()] = v.strip()
        else:
            t["setup"].append({"kind": m.group(1), "path": m.group(2)})
    if m := re.search(r"(?m)^[ \t]*PORT[ \t]*=[ \t]*(\d+)", setup):
        t["env"]["PORT"] = m.group(1)

    role = t["role"]
    t["model"], t["effort"] = parse_model(field(head, "Model"))
    if role in ("tester", "deployer") and (tg := field(head, "Target")):
        if tg not in ("staging", "prod"):
            raise TaskError(f"Target '{tg}': expected staging or prod")
        t["target"] = tg
    if role == "developer":
        t["repos"] = task_repos(head, role)
        t["workdir"] = t["worktree"]
    elif role == "tester":
        m = re.match(r"^(T-\d+)\s*@\s*(.+)$", field(head, "Verifies") or "")
        if not m:
            raise TaskError('tester task needs "Verifies: T-xxx @ <SHA>" (workspace: "T-xxx @ <repo>@<SHA>, ...")')
        cf = task_file(m.group(1))
        ct = cf.read_text(encoding="utf-8-sig")
        ch = header(ct)
        t["checked"] = {"id": m.group(1), "file": str(cf), "branch": field(ch, "Branch"),
                        "worktree": field(ch, "Worktree"), "result": section(ct, "Result")}
        if not t["checked"]["worktree"]:
            raise TaskError(f"checked task {cf.name} has no Worktree")
        t["checked"]["repos"] = task_repos(ch, "developer")
        t["repos"] = task_repos(head, role, t["checked"]["repos"])
        t["shas"] = parse_shas(m.group(2), t["repos"])
        if not t["shas"]:
            raise TaskError(f"Verifies must name one commit per repository: {', '.join(r or '<SHA>' for r in t['repos'])}")
        t["sha"] = sha_text(t["shas"])
        t["workdir"] = f"{t['checked']['worktree']}.{tid.lower()}"  # disposable checkout of the checked commit
    elif role == "deployer":
        t["repos"] = task_repos(head, role) or workspace_repos() or [""]
        shas = parse_shas(field(head, "Deploys"), t["repos"])
        if not shas:  # a workspace deployer may deploy a subset: take the repositories it names
            pairs = re.findall(rf"([^\s,@]+)\s*@\s*({SHA})", field(head, "Deploys") or "")
            shas = {k: v.lower() for k, v in pairs} if pairs else None
        if not shas:
            raise TaskError('deployer task needs "Deploys: <SHA>" (workspace: "<repo>@<SHA>, ...")')
        t["repos"], t["shas"], t["sha"] = sorted(shas), shas, sha_text(shas)
        if t["target"] == "local":
            raise TaskError('deployer task needs "Target: staging | prod"')
        t["workdir"] = str(ROOT)
    else:
        raise TaskError(f"Role '{role}': expected developer, tester or deployer")
    t["checkouts"] = checkouts(t)
    return t


def baseline(t):
    """What the worker must not change during an attempt (protocol: Review isolation)."""
    b = {"taskHash": t["headerHash"]}
    if t["role"] == "tester":
        c = t["checked"]
        refs, trees = [], []
        for r in c["repos"]:
            refs.append(git("rev-parse", "--verify", "--quiet", f"{c['branch']}^{{commit}}", cwd=repo_dir(r)) if c["branch"] else None)
            wt = Path(sub_path(c["worktree"], r))
            trees.append((git("rev-parse", "HEAD", cwd=wt) or "") + (git("status", "--porcelain", cwd=wt) or "") if wt.exists() else None)
        b["checkedRef"] = refs[0] if len(refs) == 1 else json.dumps(refs)
        b["checkedTree"] = (sha256(trees[0]) if trees[0] is not None else None) if len(trees) == 1 else sha256(json.dumps(trees))
        b["checkedFileHash"] = sha256(Path(c["file"]).read_text(encoding="utf-8-sig"))
    return b


def result_field(t, name):
    return field(t["result"], name)


def ledger_rows():
    path = ledger.ledger_path()
    if not path.exists():
        return {}
    _, cols, rows, _, _ = ledger.load(path)
    return {r[0]: dict(zip(cols, r)) for r in rows}


def runtime(tid):
    p = RUNTIME / f"{tid}.json"
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else None


def last_attempt(tid):
    rt = runtime(tid)
    return rt["attempts"][-1] if rt and rt.get("attempts") else None


# --- preflight: all problems at once, before anything is created (protocol: Launching workers, rule 9)
def preflight(tid, manual, live, tool=None):
    bad = []
    try:
        t = parse(tid)
    except TaskError as e:
        return None, [str(e)]
    tpl = (TASKS / "_template.md").read_text(encoding="utf-8-sig")
    led = ledger_rows()
    status = {k: v.get("Status", "") for k, v in led.items()}
    role, pre_merge = t["role"], t["role"] == "tester" and t["target"] == "local"

    if (led.get(tid, {}).get("Notes") or "").startswith("awaiting approval"):
        bad.append(f"{tid} awaits the human's approval of the plan: python .agentflow/tools/ledger.py approve {tid} once approved")
    if status.get(tid) not in ("ready", "in progress", "review", "blocked"):
        bad.append(f"{tid}: missing or final ledger status; cannot launch")
    if not manual and role == "deployer":
        bad.append("a Deployer runs only in the session the human designated: use -Manual")
    if not manual and role == "tester" and t["target"] == "prod":
        bad.append("a live Tester on prod runs only in the session the human designated: use -Manual")
    if role == "developer":
        if not t["branch"] or not t["worktree"]:
            bad.append("developer task needs Branch and Worktree")
        elif not t["branch"].startswith(tid.lower() + "-"):
            bad.append(f"Branch '{t['branch']}' is not named after the task ({tid.lower()}-slug)")
        if not re.match(r"^(tester|none\b.*\S.*)$", t["independent"] or ""):
            bad.append('developer task needs "Independent check: tester | none - <reason>"')
    for s, own in (("Acceptance criteria", t["acceptance"]), ("Checks", bullets(section(Path(t["file"]).read_text(encoding="utf-8-sig"), "Checks")))):
        if not [b for b in own if b not in bullets(section(tpl, s))]:
            bad.append(f"## {s} is empty or still the template text")
    bad += [f"env: {k} is set by the launcher, not by a Task File" for k in t["env"] if k.startswith("AGENTFLOW_")]
    bad += [f"Allowed files '{a}' overlaps Do not touch '{d}'" for a in t["allowed"] for d in t["denied"] if overlap(a, d)]
    for s in t["setup"]:
        if not (ROOT / s["path"]).exists():
            bad.append(f"Setup {s['kind']}: {s['path']} is not in the main folder; build it there first")

    checked = t.get("checked", {}).get("id")
    for d in t["depends"]:
        if d == checked and pre_merge:
            continue  # pre-merge tester: the checked task is in review, not done
        if status.get(d) != "done":
            bad.append(f"Depends on {d} is '{status.get(d, '')}' in the ledger, needs 'done'")
    if role == "tester":
        c = t["checked"]
        if sorted(t["repos"]) != sorted(c["repos"]):
            bad.append(f"Repo {', '.join(t['repos'])} differs from the checked task's {', '.join(c['repos'])}")
        if pre_merge:
            for r, sha in t["shas"].items():
                head = git("rev-parse", "--verify", "--quiet", f"{c['branch']}^{{commit}}", cwd=repo_dir(r)) if c["branch"] else None
                if not head or not head.startswith(sha):
                    bad.append(f"branch '{c['branch']}'{' in ' + r if r else ''} is at '{head}', task verifies {sha}")
            change = parse_shas(field(c["result"], "Change"), c["repos"])
            if field(c["result"], "Outcome") != "completed":
                bad.append(f"checked task {checked} has no Result with Outcome: completed")
            elif not change or any(not change.get(r, "").startswith(sha[:7]) and not sha.startswith(change.get(r, "-"))
                                   for r, sha in t["shas"].items()):
                bad.append(f"checked task {checked} Result Change is not {t['sha']}")
        else:
            bad += [f"commit {sha}{' in ' + r if r else ''} is not merged into the main branch"
                    for r, sha in t["shas"].items() if not git_ok("merge-base", "--is-ancestor", sha, "HEAD", cwd=repo_dir(r))]
        if checked in live:
            bad.append(f"checked task {checked} still has a live worker")
    if role == "deployer":
        bad += [f"commit {sha}{' in ' + r if r else ''} is not merged into the main branch"
                for r, sha in t["shas"].items() if not git_ok("merge-base", "--is-ancestor", sha, "HEAD", cwd=repo_dir(r))]

    # other tasks: issued and not accepted (ledger) or with a worker process (runtime, also mid-launch or manual)
    open_ids = sorted({k for k, v in status.items() if v in ("in progress", "review")} | set(live))
    for oid in open_ids:
        if oid in (tid, checked):
            continue
        try:
            o = parse(oid)
        except TaskError as e:  # unknown never passes
            bad.append(f"open task {oid}: {e}")
            continue
        bad += [f"Allowed files '{a}' overlaps {oid} '{b}' (not merged yet)" for a in t["allowed"] for b in o["allowed"] if overlap(a, b)]
        bad += [f"Rebuild together '{r}' is shared with {oid}" for r in t["rebuild"] if r in o["rebuild"]]
        if oid in live and t["env"].get("PORT") and t["env"].get("PORT") == o["env"].get("PORT"):
            bad.append(f"PORT={t['env']['PORT']} is used by running {oid}")

    # project rules: "## Preflight" in .agentflow/docs/engineering-rules.md and/or AGENTS.md
    #   - deny: <regex>                  no Checks command or Setup line may match (local tasks)
    #   - require: <regex> => <regex>    a Checks command matching the first must match the second (local tasks)
    #   - parallel: <tool>=<n>           at most n running attempts of that tool on this machine (any task)
    text = Path(t["file"]).read_text(encoding="utf-8-sig")
    run = t["checks"] + bullets(section(text, "Setup"))
    for src in (FLOW_ROOT / "docs" / "engineering-rules.md",
                FLOW_ROOT / "docs" / "project-rules.md", ROOT / "AGENTS.md"):
        if not src.exists():
            continue
        for r in bullets(section(src.read_text(encoding="utf-8-sig"), "Preflight")):
            try:
                if m := re.match(r"^parallel\s*:\s*`?([a-z]+)\s*=\s*(\d+)`?$", r):
                    if tool == m.group(1):
                        busy = sorted(o for o in live if o != tid and ((last_attempt(o) or {}).get("tool") == tool))
                        if len(busy) >= int(m.group(2)):
                            bad.append(f"{tool} allows {m.group(2)} running session(s) on this machine (project rule); "
                                       f"running: {', '.join(busy)}")
                elif m := re.match(r"^deny\s*:\s*`?(.+?)`?$", r):
                    if t["target"] == "local":
                        bad += [f"'{c}' matches project deny rule '{m.group(1)}'" for c in run if re.search(m.group(1), c)]
                elif m := re.match(r"^require\s*:\s*`?(.+?)`?\s*=>\s*`?(.+?)`?$", r):
                    if t["target"] == "local":
                        bad += [f"'{c}' must match '{m.group(2)}' (project rule for '{m.group(1)}')"
                                for c in t["checks"] if re.search(m.group(1), c) and not re.search(m.group(2), c)]
                else:
                    bad.append(f"project Preflight: unknown rule '{r}'")
            except re.error as e:
                bad.append(f"project Preflight: bad rule '{r}': {e}")
    return t, bad


def worktree_copy_problems(t):
    """A developer wrote its Result into the Task File copy inside its worktree, or committed Task Files on its branch.
    The main Task File is the only one read at acceptance; a commit under .agentflow/tasks/ moves the branch past
    Change and would merge memory through a product branch."""
    bad = []
    for c in t["checkouts"]:
        copy = Path(c["path"]) / t["rel"]
        if copy.is_file() and section(copy.read_text(encoding="utf-8-sig"), "Result").strip() and not t["result"].strip():
            bad.append(f"Result written in the worktree copy {copy}, not in {t['file']}: copy it there verbatim, "
                       "and drop any commit of it from the branch (protocol: Runtime state)")
        main = git("rev-parse", "--abbrev-ref", "HEAD", cwd=c["repo"]) or "main"
        if Path(c["path"]).is_dir():
            # Walk unique commits without path-limited history simplification. Ordinary commits
            # include edits later reverted; merges count only changes to the automatic merge.
            commits = git("rev-list", "--parents", f"{main}..HEAD", cwd=c["path"])
            for line in commits.splitlines():
                sha, *parents = line.split()
                if len(parents) > 2:
                    bad.append(f"cannot audit octopus merge {sha}: use two-parent merges")
                    continue
                args = (("show", "--remerge-diff", "--format=", sha) if len(parents) == 2
                        else ("diff-tree", "--root", "--no-commit-id", "-p", sha))
                result = subprocess.run(["git", *args, "--", ".agentflow/tasks"], cwd=c["path"],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace")
                if result.returncode:
                    bad.append(f"cannot audit Task Files in {sha}: {result.stderr.strip()}")
                elif result.stdout.strip():
                    bad.append(f"branch {t['branch']} commits Task Files ({sha}): only the main folder holds them")
    return bad


def endcheck(tid):
    """Compare the end of an attempt with its baseline. Returns the violations."""
    att = last_attempt(tid)
    if not att or "baseline" not in att:
        return ["no attempt baseline in runtime state"]
    t, then = parse(tid), att["baseline"]
    now = baseline(t)
    bad = []
    if now["taskHash"] != then.get("taskHash"):
        bad.append("Task File changed above ## Result")
    if t["role"] == "developer":
        bad += worktree_copy_problems(t)
    if t["role"] == "tester":
        c = t["checked"]
        if now["checkedRef"] != then.get("checkedRef"):
            bad.append(f"review isolation: branch {c['branch']} moved")
        if now["checkedTree"] != then.get("checkedTree"):
            bad.append(f"review isolation: worktree {c['worktree']} changed")
        if now["checkedFileHash"] != then.get("checkedFileHash"):
            bad.append(f"review isolation: Task File of {c['id']} changed")
    return bad


def shell(cmd):
    if os.name == "nt":
        return [shutil.which("pwsh") or "powershell", "-NoProfile", "-Command", cmd]
    return ["sh", "-c", cmd]


def run_checks(t, cwd, log):
    env = {**os.environ, **t["env"], "AGENTFLOW_TARGET": "local"}
    out = []
    with open(log, "a", encoding="utf-8") as fh:
        for c in t["checks"]:
            fh.write(f"\n$ {c}\n")
            fh.flush()
            r = subprocess.run(shell(c), cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT)
            out.append({"cmd": c, "exit": r.returncode})
    return out


def verdict_problems(result):
    v = field(result, "Verdict")
    if v not in VERDICTS:
        return [f"Result Verdict '{v}': expected {' | '.join(VERDICTS)}"], v
    found = re.findall(r"(?m)^[ \t]*-.*?[ \t]-[ \t](pass|partial|unverified|fail)\b", result)  # "- <criterion> - <verdict> - ..."
    worst = max(found, key=VERDICTS.index) if found else None
    if worst and VERDICTS.index(worst) > VERDICTS.index(v):
        return [f"Verdict {v} but a criterion is {worst} (overall = worst criterion)"], v
    return [], v


# --- verify: acceptance evidence for one task (protocol: Acceptance)
def verify(tid):
    t = parse(tid)
    att = last_attempt(tid)
    bad, checks, flagged = [], [], []
    RUNTIME.mkdir(parents=True, exist_ok=True)
    vpath = RUNTIME / f"{tid}.verify.json"
    records = json.loads(vpath.read_text(encoding="utf-8")) if vpath.exists() else []
    n = len(records) + 1
    log = RUNTIME / f"{tid}.verify.{n}.log"

    if not att:
        bad.append("no attempt in runtime state (every attempt starts through .agentflow/tools/run-task.ps1)")
    elif att.get("status") != "exited":
        bad.append(f"last attempt {att.get('n')} is '{att.get('status')}', needs 'exited'")
    if att and att.get("baseline", {}).get("taskHash") != t["headerHash"]:
        bad.append("Task File changed above ## Result since the attempt started")
    outcome = result_field(t, "Outcome")
    if outcome != "completed":
        bad.append(f"Result Outcome is '{outcome}', needs 'completed'")
    sha = t.get("sha")

    if t["role"] == "developer":
        shas = parse_shas(result_field(t, "Change"), t["repos"])
        sha = None
        if not shas:
            bad.append('Result needs "Change: <commit SHA>"' if t["repos"] == [""] else
                       f'Result needs "Change: {", ".join(r + "@<SHA>" for r in t["repos"])}"')
        else:
            changed = []
            for r in list(shas):  # a short SHA in the Result: record the full one, as the ledger and testers expect
                if len(shas[r]) < 40:
                    shas[r] = (git("rev-parse", "--verify", "--quiet", f"{shas[r]}^{{commit}}", cwd=repo_dir(r)) or shas[r]).lower()
            sha = sha_text(shas)
            for r, rsha in shas.items():
                where = f" in {r}" if r else ""
                wt = Path(sub_path(t["worktree"] or "", r))
                if (head := git("rev-parse", "--verify", "--quiet", f"{t['branch']}^{{commit}}", cwd=repo_dir(r))) is None or not head.startswith(rsha):
                    bad.append(f"branch {t['branch']}{where} is at '{head}', Result Change is {rsha}")
                elif not wt.exists() or not (git("rev-parse", "HEAD", cwd=wt) or "").startswith(rsha) or git("status", "--porcelain", cwd=wt):
                    bad.append(f"worktree {wt} must exist, be clean and at {rsha}")
                else:
                    prefix = f"{r.lower()}/" if r else ""
                    changed += [prefix + p.lower() for p in (git("diff", "--name-only", f"{main_branch(r)}...{rsha}", cwd=repo_dir(r)) or "").splitlines() if p]
            if not bad:
                bad += [f"changed file outside Allowed files: {p}" for p in changed if not any(overlap(a, p) for a in t["allowed"])]
                flagged = [p for p in changed if any(p in c.replace("\\", "/").lower() for c in t["checks"])]
                checks = run_checks(t, Path(t["worktree"]), log)
                bad += [f"check failed (exit {c['exit']}): {c['cmd']}" for c in checks if c["exit"]]
                ind = t["independent"] or ""
                if ind.startswith("tester"):
                    want = (tid, sha_text(shas, short=True))
                    ok_testers = [k for k, row in ledger_rows().items() if row.get("Status") == "done" and row.get("Role") == "tester"
                                  and _verifies(k) == want]
                    if not ok_testers:
                        bad.append(f"independent check pending: no done tester task with Verdict pass for {tid} @ {want[1]}")
                elif not ind.startswith("none"):
                    bad.append('Task File needs "Independent check: tester | none - <reason>"')
    elif t["role"] == "tester":
        vb, v = verdict_problems(t["result"])
        bad += vb
    elif t["role"] == "deployer":
        if result_field(t, "Deployment") != "deployed":
            bad.append(f"Result Deployment is '{result_field(t, 'Deployment')}', needs 'deployed'")
        if not re.search(r"\bpass\b", result_field(t, "Smoke") or ""):
            bad.append("Result Smoke is not pass")
        if t["target"] == "prod":
            ap = result_field(t, "Approval") or ""
            short = sha_text(t["shas"], short=True)
            if not (re.search(r"source=human\b.*target=prod\b", ap) and re.search(r"\bat=\S+", ap)
                    and all(v[:7] in ap for v in t["shas"].values())):
                bad.append(f'prod needs "Approval: source=human target=prod sha={short} at=<time>" from the human in the Deployer session')

    rec = {"n": n, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "attempt": att.get("n") if att else None,
           "role": t["role"], "sha": sha, "target": t["target"], "ok": not bad, "problems": bad, "checks": checks,
           "checksTouchedByTask": flagged, "log": str(log) if checks else None}
    records.append(rec)
    vpath.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"{tid} verify #{n}: {'PASS' if not bad else 'FAIL'} (sha {sha}, attempt {rec['attempt']})")
    for b in bad:
        print(f"  - {b}")
    for c in checks:
        print(f"  check exit {c['exit']}: {c['cmd']}")
    if flagged:
        print(f"  note: Checks use files changed by this task ({', '.join(flagged)}): read that diff before accepting")
    return not bad


def _verifies(tid):  # (checked id, short commits) of a tester task whose Result Verdict is pass, else None
    try:
        t = parse(tid)
    except TaskError:
        return None
    if t["role"] != "tester" or field(t["result"], "Verdict") != "pass":
        return None
    return t["checked"]["id"], sha_text(t["shas"], short=True)


def stage(n):
    rows = [r for r in ledger_rows().values() if r.get("Stage") == str(n) and r.get("Status") not in ("rejected", "cancelled")]
    bad = [f"{r['ID']} is '{r['Status']}', needs 'done'" for r in rows if r["Status"] != "done"]
    if not rows:
        bad.append(f"no tasks for Stage {n} in the ledger")
    log = RUNTIME / f"stage-{n}.log"
    RUNTIME.mkdir(parents=True, exist_ok=True)
    log.write_text("", encoding="utf-8")
    for r in rows:
        try:
            t = parse(r["ID"])
        except TaskError as e:
            bad.append(f"{r['ID']}: {e}")
            continue
        if t["role"] == "developer":
            bad += [f"{r['ID']} check failed on {main_branch()} (exit {c['exit']}): {c['cmd']}" for c in run_checks(t, ROOT, log) if c["exit"]]
    print(f"Stage {n}: {'PASS' if not bad else 'FAIL'} ({len(rows)} tasks, log {log})")
    for b in bad:
        print(f"  - {b}")
    return not bad


def portable(obj):
    """JSON for run-task.ps1: PowerShell's ConvertFrom-Json rejects an empty property name, and the single repository's
    key is "" - write it as "." (a JSON reader in Python sees the same data either way)."""
    if isinstance(obj, dict):
        return {(k if k != "" else "."): portable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [portable(v) for v in obj]
    return obj


def write(out, obj):
    data = json.dumps(portable(obj), indent=2)
    if out:
        Path(out).write_text(data, encoding="utf-8")
    else:
        print(data)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("task", "preflight", "endcheck", "verify"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--out")
        if name == "preflight":
            p.add_argument("--manual", action="store_true")
            p.add_argument("--live", default="")
            p.add_argument("--tool", help="the tool about to be launched (project rule parallel)")
    sub.add_parser("stage").add_argument("n")
    a = ap.parse_args()
    try:
        if a.cmd == "task":
            t = parse(a.id)
            t["baseline"] = baseline(t)
            write(a.out, t)
        elif a.cmd == "preflight":
            t, bad = preflight(a.id, a.manual, {x for x in a.live.split(",") if x}, a.tool)
            write(a.out, {"ok": not bad, "problems": bad, "task": t})
            return 0 if not bad else 1
        elif a.cmd == "endcheck":
            write(a.out, {"violations": endcheck(a.id)})
        elif a.cmd == "verify":
            return 0 if verify(a.id) else 1
        elif a.cmd == "stage":
            return 0 if stage(a.n) else 1
    except TaskError as e:
        print(f"gate: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
