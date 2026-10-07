"""Accept one task after its Result: verify, merge, ledger, cleanup - stopping at the first failure.

  python .agentflow/tools/accept.py T-007            # accept a developer or tester task
  python .agentflow/tools/accept.py T-007 --dry-run  # print the steps without changing anything

Rules: .agentflow/docs/ai-handoff-protocol.md, "Task lifecycle" and "Git rules". Only the Orchestrator (or a
Single Mode session) runs this, from the main folder on the main branch.

Developer: gate.py verify -> git merge --no-ff <branch> -> ledger review/done -> git worktree remove -> git branch -d.
Tester:    gate.py verify -> ledger review/done with the Verifies SHA (nothing to merge).
Every step runs only when the previous one succeeded; a failed merge is aborted. The printed log says exactly which
steps ran, so a partial run can be finished by hand. Nothing is force-deleted: worktree removal and `git branch -d`
refuse unmerged or dirty state, and that refusal is reported, not overridden.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import FLOW_ROOT, REPO_ROOT  # noqa: E402
import gate  # noqa: E402
import ledger  # noqa: E402

TOOLS = FLOW_ROOT / "tools"


class Stop(Exception):
    pass


def run(cmd, dry, cwd=REPO_ROOT, check=True):
    print(("  (dry run) " if dry else "  $ ") + " ".join(str(c) for c in cmd))
    if dry:
        return ""
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout + p.stderr).strip()
    if out:
        print("    " + out.replace("\n", "\n    "))
    if check and p.returncode:
        raise Stop(f"step failed (exit {p.returncode}): {' '.join(str(c) for c in cmd)}")
    return p.stdout


def status_of(tid):
    return ledger_rows().get(tid, {}).get("Status")


def ledger_rows():
    path = ledger.ledger_path()
    if not path.exists():
        return {}
    _, cols, rows, _, _ = ledger.load(path)
    return {r[0]: dict(zip(cols, r)) for r in rows}


def to_done(tid, sha, dry):
    py = sys.executable
    st = status_of(tid)
    if st == "done":
        print(f"  ledger: {tid} is already done")
        return
    if st != "review":
        run([py, TOOLS / "ledger.py", "set", tid, "--status", "review"], dry)
    run([py, TOOLS / "ledger.py", "set", tid, "--status", "done", "--commit", sha], dry)


def accept(tid, dry):
    t = gate.parse(tid)
    main = gate.main_branch()
    print(f"{tid}: role {t['role']}, main folder on '{main}'")
    dirty = [l for l in (gate.git("status", "--porcelain") or "").splitlines()
             if l and not l[3:].replace("\\", "/").startswith(".agentflow/") and not l[3:].startswith('".agentflow/')]
    if dirty:
        raise Stop("the main folder has uncommitted changes outside .agentflow/: " + "; ".join(dirty[:5]))

    with tempfile.TemporaryDirectory() as tmp:
        print("1. verify")
        run([sys.executable, TOOLS / "gate.py", "verify", tid, "--out", Path(tmp) / "verify.json"], dry)

    if t["role"] == "tester":
        print("2. ledger")
        to_done(tid, t["sha"], dry)
        print(f"{tid} accepted (tester; nothing to merge).")
        return

    if t["role"] != "developer":
        raise Stop(f"role '{t['role']}': accept handles developer and tester tasks; a deployer is closed by hand")

    m = gate.re.match(rf"^({gate.SHA})", gate.result_field(t, "Change") or "")
    if not m:
        raise Stop('Result needs "Change: <commit SHA>"')
    sha = gate.git("rev-parse", "--verify", "--quiet", f"{m.group(1)}^{{commit}}") or m.group(1)
    title = ledger_rows().get(tid, {}).get("Title", "").strip()

    print("2. merge")
    if gate.git_ok("merge-base", "--is-ancestor", sha, "HEAD"):
        print(f"  {t['branch']} is already merged into {main}")
    else:
        try:
            run(["git", "merge", "--no-ff", t["branch"], "-m", f"Merge {tid}: {title or t['branch']}"], dry)
        except Stop:
            run(["git", "merge", "--abort"], dry, check=False)
            raise Stop(f"merge of {t['branch']} failed and was aborted; resolve it in a successor task")

    print("3. ledger")
    to_done(tid, sha, dry)

    print("4. cleanup")
    wt = t["worktree"]
    if wt and Path(wt).exists():
        run(["git", "worktree", "remove", wt], dry)
    else:
        print(f"  worktree {wt} is already gone")
    run(["git", "worktree", "prune"], dry)
    if gate.git_ok("rev-parse", "--verify", "--quiet", f"refs/heads/{t['branch']}"):
        run(["git", "branch", "-d", t["branch"]], dry)
    else:
        print(f"  branch {t['branch']} is already deleted")
    print(f"{tid} accepted and merged into {main}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        accept(a.id, a.dry_run)
    except (Stop, gate.TaskError) as e:
        print(f"STOPPED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
