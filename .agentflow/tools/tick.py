"""One mechanical step of orchestration, without a language model; plus the orchestrator heartbeat and tool limits.

  python .agentflow/tools/tick.py run [--dry-run] [--even-if-live]   act on every open task once; writes tick.json
  python .agentflow/tools/tick.py heartbeat --holder <name>   claim or refresh orchestration (exit 1: someone else holds it)
  python .agentflow/tools/tick.py release [--holder <name>]   the Orchestrator ends its session cleanly
  python .agentflow/tools/tick.py status                      live Orchestrator and limited tools; exit 0 live, 1 not
  python .agentflow/tools/tick.py next-orchestrator           first orchestrator tool that is not limited ('' if none)
  python .agentflow/tools/tick.py limit <tool> "<message>"    record a usage limit from a tool's own message

Rules: .agentflow/docs/ai-handoff-protocol.md, section "Autonomous orchestration". `run` does by itself only what
needs no judgment: accept a tester whose Verdict is pass, accept a developer whose tester is done or whose independent
check is none, launch a ready task whose dependencies are done and whose tool is not limited. Everything else becomes
a "need" in tick.json for an Orchestrator session. Acceptance and launch go through accept.py and run-task.ps1, so
every gate still applies. While an Orchestrator is live (fresh heartbeat), `run` only reports: one actor at a time.
"""
import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import FLOW_ROOT  # noqa: E402
import gate  # noqa: E402
import machine_capacity  # noqa: E402

TOOLS = FLOW_ROOT / "tools"
RT = FLOW_ROOT / "tasks" / ".runtime"
HEARTBEAT = RT / "orchestrator.json"
LIMITS = RT / "tool-limits.json"
TICK = RT / "tick.json"
LOCK = RT / "tick.lock"
LAUNCHABLE = ("codex", "claude", "agy", "devin")
# A tool can be temporarily unusable because its quota is exhausted or because
# account access is disabled. Both cases must hand orchestration to the next
# configured tool. With no reset time in the message record_limit() retries the
# preferred tool after one hour instead of removing it from the configured list.
LIMIT_RE = re.compile(
    r"usage limit|rate limit|quota|session limit|hit your limit|out of credits|"
    r"disabled[^\r\n]*subscription access|subscription access[^\r\n]*disabled|"
    r"ask your admin to enable access",
    re.I,
)


def orchestrators():
    return [t.strip() for t in os.environ.get("AGENTFLOW_ORCHESTRATORS", "claude,codex,devin,agy").split(",") if t.strip()]


def stale_minutes():
    return int(os.environ.get("AGENTFLOW_ORCHESTRATOR_STALE_MINUTES", "20"))


def now():
    return datetime.now(timezone.utc)


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# --- tool limits -------------------------------------------------------------------------------------------------
def parse_reset(text, at=None):
    """When a limited tool is usable again, from its own message; one hour later when the message does not say."""
    at = at or now()
    if m := re.search(r"resets?\s+in\s+(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", text, re.I):
        d, h, mi = (int(x or 0) for x in m.groups())
        if d or h or mi:
            return at + timedelta(days=d, hours=h, minutes=mi)
    if m := re.search(r"(?:resets?|try again)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)", text, re.I):
        hour, minute = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0), int(m.group(2) or 0)
        local = at.astimezone()
        t = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if t <= local:
            t += timedelta(days=1)
        return t.astimezone(timezone.utc)
    return at + timedelta(hours=1)


def record_limit(tool, text, at=None, seen=None):
    lim = read(LIMITS, {})
    until = parse_reset(text, at)
    lim[tool] = {"until": iso(until), "reason": " ".join(text.split())[-200:], "at": iso(at or now())}
    if seen:
        lim.setdefault("_seen", {})[seen] = iso(at or now())
    write(LIMITS, lim)
    machine_capacity.record_limit(tool, until)
    return lim[tool]


def limit_end(tool):
    local = read(LIMITS, {}).get(tool)
    local_end = parse_iso(local["until"]) if local else None
    global_end = machine_capacity.limit_until(tool)
    return max((end for end in (local_end, global_end) if end), default=None)


def limited(tool, at=None):
    end = limit_end(tool)
    return bool(end) and end > (at or now())


def next_orchestrator(at=None, skip=()):
    return next((t for t in orchestrators() if t not in skip and not limited(t, at)
                 and machine_capacity.has_capacity(t, FLOW_ROOT.parent, at)), "")


# --- heartbeat ---------------------------------------------------------------------------------------------------
def heartbeat(holder, force=False):
    """Claim or refresh orchestration. Refused (returns the other holder) while a different holder is live."""
    other = live_orchestrator()
    if other and other.get("holder") != holder and not force:
        return other
    write(HEARTBEAT, {"holder": holder, "at": iso(now())})
    return None


def release(holder=None):
    hb = read(HEARTBEAT, None)
    if hb and (holder is None or hb.get("holder") == holder):
        HEARTBEAT.unlink(missing_ok=True)


def live_orchestrator(at=None):
    hb = read(HEARTBEAT, None)
    if not hb or not hb.get("at"):
        return None
    return hb if (at or now()) - parse_iso(hb["at"]) < timedelta(minutes=stale_minutes()) else None


# --- worker processes --------------------------------------------------------------------------------------------
def alive(pid, pid_start=None):
    """The worker process still runs. pid_start (.NET UTC ticks, written by run-task.ps1) guards against pid reuse."""
    if not pid:
        return False
    if os.name == "nt":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code, ft = ctypes.c_ulong(), [ctypes.c_ulonglong() for _ in range(4)]
            if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return False
            if pid_start and k.GetProcessTimes(h, *(ctypes.byref(x) for x in ft)):
                return ft[0].value + 504911232000000000 == int(pid_start)  # FILETIME (1601) -> .NET ticks (0001)
            return True
        finally:
            k.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def log_tail(att, n=4000):
    p = Path(att.get("log") or "")
    try:
        return p.read_text(encoding="utf-8", errors="replace")[-n:] if p.is_file() else ""
    except OSError:
        return ""


def hit_limit(att):
    return bool(att.get("limitHit")) or (att.get("status") in ("exited", "error") and bool(LIMIT_RE.search(log_tail(att, 1500))))


# --- decisions ---------------------------------------------------------------------------------------------------
def testers_of(dev_id, rows):
    """(tester id, ledger status) of every tester task that checks this developer task."""
    out = []
    for k, r in rows.items():
        if r.get("Role") != "tester" or r.get("Status") in ("rejected", "cancelled"):
            continue
        try:
            if gate.parse(k)["checked"]["id"] == dev_id:
                out.append((k, r.get("Status")))
        except (gate.TaskError, OSError):
            continue
    return out


def decide(rows, at=None):
    """[(task, action, detail)], action: accept | launch | wait | need. Reads files, changes nothing."""
    out = []
    done = {k for k, r in rows.items() if r.get("Status") == "done"}
    for tid, r in rows.items():
        st = r.get("Status")
        if st == "blocked" and (r.get("Notes") or "").startswith("awaiting approval"):
            out.append((tid, "wait", "awaiting the human's approval of the plan (ledger.py approve)"))
            continue
        if st in ("done", "rejected", "cancelled", "blocked"):
            continue
        try:
            t = gate.parse(tid)
        except (gate.TaskError, OSError) as e:
            out.append((tid, "need", f"Task File: {e}"))
            continue
        att = gate.last_attempt(tid)
        running = bool(att) and att.get("status") == "running"
        if running and not (att.get("manual") or alive(att.get("pid"), att.get("pidStart"))):
            out.append((tid, "need", "the worker process is gone without a final state (dead attempt): recover it"))
            continue
        if running:
            out.append((tid, "wait", f"{att.get('tool')} is working (attempt {att.get('n')})"))
            continue
        if st == "ready":
            deps = [d for d in t["depends"] if d not in done]
            tool = (r.get("Tool") or "").strip()
            if deps:
                out.append((tid, "wait", f"depends on {', '.join(deps)}"))
            elif tool not in LAUNCHABLE:
                out.append((tid, "need", f"Tool '{tool}' cannot be started automatically: route it or start it by hand"))
            elif limited(tool, at):
                out.append((tid, "need", f"its tool {tool} is limited until {iso(limit_end(tool))}: move it to another tool or wait"))
            else:
                out.append((tid, "launch", tool))
            continue
        if not att:
            out.append((tid, "need", f"status '{st}' without any attempt"))
            continue
        if hit_limit(att):
            out.append((tid, "need", f"{att.get('tool')} hit a usage limit in attempt {att.get('n')}: relaunch on a fallback tool"))
            continue
        outcome = gate.result_field(t, "Outcome")
        if t["role"] == "tester":
            verdict = gate.result_field(t, "Verdict")
            if outcome == "completed" and verdict == "pass":
                out.append((tid, "accept", "tester, Verdict pass"))
            else:
                out.append((tid, "need", f"tester Outcome {outcome or 'missing'}, Verdict {verdict or 'missing'}: decide on {t['checked']['id']}"))
        elif t["role"] == "developer":
            if outcome != "completed":
                out.append((tid, "need", f"developer Outcome {outcome or 'missing'} (attempt {att.get('status')}): decide"))
            elif (t["independent"] or "").lower().startswith("none"):
                out.append((tid, "accept", "developer, no independent check"))
            elif testers := testers_of(tid, rows):
                if any(s == "done" for _, s in testers):
                    out.append((tid, "accept", "developer, its tester is done"))
                else:
                    out.append((tid, "wait", f"its tester {', '.join(k for k, _ in testers)} is not done"))
            else:
                out.append((tid, "need", "developer completed: write its tester task"))
        else:
            out.append((tid, "need", f"{t['role']} task: closed by the Orchestrator"))
    return out


# --- acting ------------------------------------------------------------------------------------------------------
def learn_limits(rows):
    """Record limits from finished attempts once, so later launches and the conductor skip those tools."""
    seen = read(LIMITS, {}).get("_seen", {})
    for tid in rows:
        att = gate.last_attempt(tid)
        if not att or att.get("tool") in (None, "manual") or not hit_limit(att):
            continue
        key = f"{tid}#{att.get('n')}"
        if key not in seen:
            record_limit(att["tool"], log_tail(att, 1500), at=parse_iso(att["finishedAt"]) if att.get("finishedAt") else None, seen=key)


def py(*args):
    p = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def do_accept(tid):
    code, out = py(TOOLS / "accept.py", tid)
    return code == 0, (out.splitlines() or [""])[-1]


def do_launch(tid, tool):
    ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    p = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TOOLS / "run-task.ps1"), tid, tool],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout + p.stderr).strip()
    if p.returncode:
        return False, out[-300:]
    code, lo = py(TOOLS / "ledger.py", "set", tid, "--status", "in progress")
    return True, tool if code == 0 else f"{tool} started, but the ledger refused 'in progress': {lo[-200:]}"


def lock():
    """One tick at a time (the conductor and a background Orchestrator may both call run). Stale after 30 minutes."""
    RT.mkdir(parents=True, exist_ok=True)
    try:
        if LOCK.exists() and now().timestamp() - LOCK.stat().st_mtime > 1800:
            LOCK.unlink()
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def run(dry=False, even_if_live=False, accept=do_accept, launch=do_launch):
    live = live_orchestrator()
    report_only = dry or (live is not None and not even_if_live)
    if not report_only and not lock():
        return {"at": iso(now()), "skipped": "another tick is running"}
    try:
        rows = gate.ledger_rows()
        if not dry:
            learn_limits(rows)
        acted, failed, busy = [], [], []
        if not report_only:
            for _ in range(len(rows) + 1):  # accept until nothing changes: a tester accepted can unlock its developer
                todo = [(t, d) for t, a, d in decide(rows) if a == "accept" and t not in {f["task"] for f in failed}]
                if not todo:
                    break
                for tid, detail in todo:
                    ok, msg = accept(tid)
                    (acted if ok else failed).append({"task": tid, "action": "accept", "detail": msg if ok else f"accept stopped: {msg}"})
                rows = gate.ledger_rows()
            for tid, a, tool in decide(rows):
                if a == "launch":
                    ok, msg = launch(tid, tool)
                    if not ok and ("running session(s) on this machine" in msg or "machine capacity wait:" in msg):
                        busy.append({"task": tid, "detail": f"{tool} has no free slot (project rule parallel)"})
                        continue
                    (acted if ok else failed).append({"task": tid, "action": "launch", "detail": msg if ok else f"launch refused: {msg}"})
            rows = gate.ledger_rows()
        plan = decide(rows)
        failed_ids = {f["task"] for f in failed}
        needs = failed + [{"task": t, "action": "need", "detail": d} for t, a, d in plan if a == "need" and t not in failed_ids]
        report = {"at": iso(now()), "reportOnly": report_only, "live": live, "acted": acted,
                  "would": [{"task": t, "action": a, "detail": d} for t, a, d in plan if a in ("accept", "launch")] if report_only else [],
                  "needs": needs, "waits": busy + [{"task": t, "detail": d} for t, a, d in plan if a == "wait"],
                  "nextOrchestrator": next_orchestrator(),
                  "limited": {k: v["until"] for k, v in read(LIMITS, {}).items() if k != "_seen" and limited(k)}}
        if not dry:
            write(TICK, report)
        return report
    finally:
        if not report_only:
            LOCK.unlink(missing_ok=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--even-if-live", action="store_true", help="act although an Orchestrator heartbeat is fresh")
    r.add_argument("--json", action="store_true", help="print the report as JSON")
    hp = sub.add_parser("heartbeat")
    hp.add_argument("--holder", default="orchestrator")
    hp.add_argument("--force", action="store_true", help="take over from another live holder (the human's session)")
    sub.add_parser("release").add_argument("--holder", help="release only if this holder has it")
    sub.add_parser("status")
    no = sub.add_parser("next-orchestrator")
    no.add_argument("--skip", default="", help="comma-separated tools whose atomic claim just lost a race")
    lp = sub.add_parser("limit")
    lp.add_argument("tool")
    lp.add_argument("text")
    a = ap.parse_args()
    if a.cmd == "run":
        rep = run(a.dry_run, a.even_if_live)
        if a.json:
            print(json.dumps(rep, indent=2, ensure_ascii=False))
            return 0
        if rep.get("skipped"):
            print(f"skipped: {rep['skipped']}")
            return 0
        if rep["reportOnly"] and rep["live"]:
            print(f"report only: Orchestrator {rep['live']['holder']} is live (heartbeat {rep['live']['at']})")
        for x in rep["acted"]:
            print(f"{x['action']:7} {x['task']}: {x['detail']}")
        for x in rep["would"]:
            print(f"would {x['action']} {x['task']}: {x['detail']}")
        for x in rep["needs"]:
            print(f"need    {x['task']}: {x['detail']}")
        for x in rep["waits"]:
            print(f"wait    {x['task']}: {x['detail']}")
        for k, v in rep["limited"].items():
            print(f"limited {k} until {v}")
        return 0
    if a.cmd == "heartbeat":
        if other := heartbeat(a.holder, a.force):
            print(f"refused: {other['holder']} holds orchestration (heartbeat {other['at']}); read, do not act")
            return 1
        return 0
    if a.cmd == "release":
        release(a.holder)
        return 0
    if a.cmd == "status":
        hb = live_orchestrator()
        print(f"orchestrator: {hb['holder']} (heartbeat {hb['at']})" if hb else "orchestrator: none live")
        for k, v in read(LIMITS, {}).items():
            if k != "_seen" and limited(k):
                print(f"limited: {k} until {v['until']} - {v['reason'][-80:]}")
        return 0 if hb else 1
    if a.cmd == "next-orchestrator":
        print(next_orchestrator(skip={x for x in a.skip.split(",") if x}))
        return 0
    if a.cmd == "limit":
        e = record_limit(a.tool, a.text)
        print(f"{a.tool} limited until {e['until']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
