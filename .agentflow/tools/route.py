"""Recommend the tool and model for one task with TypeSafe AI (Jev), from the project's own list of options.

  python .agentflow/tools/route.py T-007              # print the recommendation
  python .agentflow/tools/route.py T-007 --apply      # also write Tool: / Model: into a task that has not started
  python .agentflow/tools/route.py T-007 --threshold 0.6

Options: `.agentflow/docs/model-options.json` (project-owned):

  {"policy": "how to choose, in words (cost, free windows, independence)",
   "options": {"<id>": {"tool": "codex|claude|agy|devin", "model": "...", "effort": "...",
                        "describe": "what it is good at", "roles": ["developer", "tester"],
                        "from": "YYYY-MM-DD", "until": "YYYY-MM-DD"}}}

An option outside its from/until window or not meant for the task's role is left out; a tester never gets the tool
of the developer task it checks while another option remains (protocol: Tool Routing, independence).

Key: environment TYPESAFE_API_KEY, or a file named by AGENTFLOW_TYPESAFE_KEY_FILE (kept outside the repository). The
key is never printed or stored. No key, no options, an API error or confidence below the threshold: the script says
so, exits 3, and the Orchestrator decides by `.agentflow/roles/tool-routing.md`. A recommendation is advice: the
Orchestrator stays responsible for the choice (protocol: Launching workers).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import FLOW_ROOT  # noqa: E402
import gate  # noqa: E402

API = os.environ.get("TYPESAFE_API_BASE", "https://api.typesafe.ai").rstrip("/") + "/v1/systemone"
OPTIONS = FLOW_ROOT / "docs" / "model-options.json"
NO_DECISION = 3


class NoDecision(Exception):
    pass


def api_key():
    if k := os.environ.get("TYPESAFE_API_KEY", "").strip():
        return k
    f = os.environ.get("AGENTFLOW_TYPESAFE_KEY_FILE", "").strip()
    if f and Path(f).exists():
        return Path(f).read_text(encoding="utf-8-sig").strip()
    raise NoDecision("no TypeSafe key: set TYPESAFE_API_KEY or AGENTFLOW_TYPESAFE_KEY_FILE")


def available(options, role, avoid_tool=None, today=None):
    today = today or date.today().isoformat()
    out = {k: v for k, v in options.items()
           if (not v.get("roles") or role in v["roles"])
           and v.get("from", "0000") <= today <= v.get("until", "9999")}
    if avoid_tool:
        other = {k: v for k, v in out.items() if v.get("tool") != avoid_tool}
        out = other or out
    return out


def describe(o):
    bits = [o.get("describe", "")]
    bits.append(f"tool {o.get('tool')}" + (f", model {o['model']}" if o.get("model") else "")
                + (f", effort {o['effort']}" if o.get("effort") else ""))
    if o.get("until"):
        bits.append(f"available until {o['until']}")
    return "; ".join(b for b in bits if b)


def task_state(t, text, policy):
    goal = gate.section(text, "Goal").strip()
    title = text.splitlines()[0].lstrip("# ").strip() if text else t["id"]
    lines = [f"Task {title}", f"Role: {t['role']}", f"Goal: {goal}",
             "Acceptance criteria:\n" + "\n".join(f"- {a}" for a in t["acceptance"]),
             f"Allowed files: {', '.join(t['allowed']) or 'none'}",
             f"Checks: {'; '.join(t['checks'])}"]
    if t.get("repos") and t["repos"] != [""]:
        lines.append(f"Repositories: {', '.join(t['repos'])}")
    if policy:
        lines.append(f"Project policy for choosing: {policy}")
    return "\n".join(lines)


def ask(state, options, key, opener=urllib.request.urlopen, tries=3):
    body = {"state": state, "model": "jev-latest", "questions": {"option": {
        "type": "choice", "instructions": "Which option should take this task, given the task and the project policy?",
        "criteria": {k: describe(v) for k, v in options.items()}}}}
    data = json.dumps(body).encode("utf-8")
    for i in range(tries):
        req = urllib.request.Request(API, data=data, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with opener(req, timeout=30) as r:
                return json.loads(r.read())["answers"]["option"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and i + 1 < tries:
                time.sleep(2 * (i + 1))
                continue
            raise NoDecision(f"TypeSafe answered HTTP {e.code}" + (" (invalid key)" if e.code == 401 else ""))
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            raise NoDecision(f"TypeSafe call failed: {type(e).__name__}")
    raise NoDecision("TypeSafe is busy (429/529)")


def set_header(path, tool, model, effort):
    text = Path(path).read_text(encoding="utf-8")
    head, sep, rest = text.partition("\n## Result")
    head = re.sub(r"(?m)^Tool:[^\n]*$", f"Tool: {tool}", head, count=1)
    model_line = f"Model: {model}" + (f", effort={effort}" if effort else "") if model else None
    if re.search(r"(?m)^Model:", head):
        head = re.sub(r"(?m)^Model:[^\n]*\n?", (model_line + "\n") if model_line else "", head, count=1)
    elif model_line:
        head = re.sub(r"(?m)^(Tool:[^\n]*\n)", rf"\1{model_line}\n", head, count=1)
    Path(path).write_text(head + sep + rest, encoding="utf-8", newline="\n")


def route(tid, apply=False, threshold=0.5, opener=urllib.request.urlopen, today=None):
    if not OPTIONS.exists():
        raise NoDecision(f"no {OPTIONS.relative_to(FLOW_ROOT.parent).as_posix()}")
    cfg = json.loads(OPTIONS.read_text(encoding="utf-8-sig"))
    t = gate.parse(tid)
    text = Path(t["file"]).read_text(encoding="utf-8-sig")
    avoid = None
    if t["role"] == "tester":
        avoid = gate.field(gate.header(Path(t["checked"]["file"]).read_text(encoding="utf-8-sig")), "Tool")
    opts = available(cfg.get("options", {}), t["role"], avoid, today)
    if not opts:
        raise NoDecision(f"no option is available today for a {t['role']} task")
    answer = ask(task_state(t, text, cfg.get("policy", "")), opts, api_key(), opener)
    pick, conf = answer.get("choice"), float(answer.get("confidence", 0))
    probs = sorted(answer.get("probabilities", {}).items(), key=lambda kv: -kv[1])
    o = opts.get(pick, {})
    rec = {"task": tid, "at": date.today().isoformat(), "choice": pick, "confidence": conf, "probabilities": dict(probs),
           "tool": o.get("tool"), "model": o.get("model"), "effort": o.get("effort"), "applied": False}
    print(f"{tid}: {pick} (confidence {conf:.2f}) -> Tool: {o.get('tool')}"
          + (f", Model: {o['model']}" + (f", effort={o['effort']}" if o.get("effort") else "") if o.get("model") else ""))
    for k, v in probs[:5]:
        print(f"  {v:.2f}  {k}")
    if conf < threshold:
        print(f"  confidence below {threshold}: decide by tool-routing.md")
        _log(tid, rec)
        return NO_DECISION
    if apply:
        status = gate.ledger_rows().get(tid, {}).get("Status")
        if status not in (None, "", "ready", "blocked"):
            raise NoDecision(f"{tid} is '{status}': --apply changes only a task that has not started")
        set_header(t["file"], o["tool"], o.get("model"), o.get("effort"))
        if status:
            subprocess.run([sys.executable, str(FLOW_ROOT / "tools" / "ledger.py"), "set", tid, "--tool", o["tool"]],
                           check=True, capture_output=True)
        rec["applied"] = True
        print(f"  applied to {Path(t['file']).name}")
    _log(tid, rec)
    return 0


def _log(tid, rec):
    rt = FLOW_ROOT / "tasks" / ".runtime"
    rt.mkdir(parents=True, exist_ok=True)
    p = rt / f"{tid}.route.json"
    hist = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    p.write_text(json.dumps(hist + [rec], indent=2), encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("id")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()
    try:
        sys.exit(route(a.id, a.apply, a.threshold))
    except (NoDecision, gate.TaskError) as e:
        print(f"{a.id}: no recommendation: {e}. Decide by .agentflow/roles/tool-routing.md")
        sys.exit(NO_DECISION)


if __name__ == "__main__":
    main()
