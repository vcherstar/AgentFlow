"""Recommend the tool and model for one task with TypeSafe AI (Jev), from the project's own list of options.

  python .agentflow/tools/route.py T-007              # print the recommendation
  python .agentflow/tools/route.py T-007 --apply      # write Tool: / Model: into a task that has not started (high band)

Built to the TypeSafe documentation (https://docs.typesafe.ai, summary in .agentflow/docs/typesafe.md); read it
before changing this file:
- state is an object with named fields, only what the decision needs, clipped well under the 32k-token state budget;
- one request asks two atomic questions in parallel - a choice "option" (intent routing) and a 3-level score
  "complexity" - and code combines them: a pick whose `max_complexity` is exceeded escalates to its `escalate_to`;
- options are object criteria (`what`, `not_for`, `examples`), the project policy goes into the instructions;
- confidence bands: >= 0.9 act (`--apply` writes the task), 0.5-0.9 recommendation for the Orchestrator to confirm,
  < 0.5 no decision (decide by tool-routing.md); the Jev version is pinned (`jev_model` in the options file).

Options: `.agentflow/docs/model-options.json` (project-owned):

  {"policy": "how to choose, in words (cost, free windows, independence)",
   "jev_model": "jev-1.13.0",
   "options": {"<id>": {"tool": "codex|claude|agy|devin", "model": "...", "effort": "...",
                        "what": "what it is good at", "not_for": "what to keep away from it", "examples": ["..."],
                        "roles": ["developer", "tester"], "from": "YYYY-MM-DD", "until": "YYYY-MM-DD",
                        "max_complexity": 0|1|2, "escalate_to": "<id>"}}}

An option outside its from/until window or not meant for the task's role is left out; a tester never gets the tool
of the developer task it checks while another option remains (protocol: Tool Routing, independence).

Key: TYPESAFE_API_KEY, or a file named by AGENTFLOW_TYPESAFE_KEY_FILE (outside the repository); never printed or
stored. Base URL: TYPESAFE_BASE_URL (default https://api.typesafe.ai). Exit: 0 high band, 4 medium band (printed, not
applied), 3 no decision. Every call prints and logs Jev's version, token usage and request id
(.agentflow/tasks/.runtime/T-NNN.route.json). A recommendation is advice: the Orchestrator owns the choice.
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

API = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/") + "/v1/systemone"
OPTIONS = FLOW_ROOT / "docs" / "model-options.json"
JEV_MODEL = "jev-1.13.0"  # pinned: the bands below are set for this version (docs: Models)
HIGH, LOW = 0.9, 0.5      # docs: Confidence - act above 0.9, confirm 0.5-0.9, a person decides below 0.5
OK, NO_DECISION, MEDIUM = 0, 3, 4
COMPLEXITY = [
    {"summary": "Small", "signals": "one area, clear steps, mechanical or well-trodden change, little risk"},
    {"summary": "Moderate", "signals": "several files within one feature, ordinary tests, known patterns"},
    {"summary": "Hard", "signals": "cross-cutting or architectural change, unfamiliar APIs, concurrency, security, "
                                   "subtle edge cases, or many interacting parts"},
]


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


def criterion(o):
    """Object-form choice criterion (docs: Advanced structure): what / not_for / examples, plus the facts."""
    c = {"what": o.get("what") or o.get("describe", "")}
    if o.get("not_for"):
        c["not_for"] = o["not_for"]
    if o.get("examples"):
        c["examples"] = o["examples"]
    c["runs_on"] = o.get("tool") + (f" with model {o['model']}" if o.get("model") else "") \
        + (f", {o['effort']} reasoning effort" if o.get("effort") else "")
    if o.get("until"):
        c["available_until"] = o["until"]
    return c


def clip(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def task_state(t, text):
    """Only what the decision needs, as named fields (docs: State)."""
    title = text.splitlines()[0].lstrip("# ").strip() if text else t["id"]
    task = {"id": t["id"], "title": clip(title, 200), "role": t["role"],
            "goal": clip(gate.section(text, "Goal"), 2000),
            "acceptance_criteria": [clip(a, 400) for a in t["acceptance"][:30]],
            "allowed_files": t["allowed"][:40], "checks": [clip(c, 200) for c in t["checks"][:20]]}
    if t.get("repos") and t["repos"] != [""]:
        task["repositories"] = t["repos"]
    return {"task": task}


def questions(options, policy):
    return {
        "option": {"type": "choice",
                   "instructions": {"question": "Which option should take this task?",
                                    "project_policy": policy or "none",
                                    "note": "Options are alternatives; weigh the task against what each is for."},
                   "criteria": {k: criterion(v) for k, v in options.items()}},
        "complexity": {"type": "score",
                       "instructions": "How hard is this task for a capable software engineer?",
                       "criteria": COMPLEXITY},
    }


def ask(state, qs, key, jev_model, opener=urllib.request.urlopen, tries=3):
    data = json.dumps({"state": state, "model": jev_model, "questions": qs}).encode("utf-8")
    for i in range(tries):
        req = urllib.request.Request(API, data=data, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with opener(req, timeout=30) as r:
                d = json.loads(r.read())
                return d["answers"], {"model": d.get("model"), "usage": d.get("usage"),
                                      "requestId": r.headers.get("x-typesafe-request-id")}
        except urllib.error.HTTPError as e:
            rid = e.headers.get("x-typesafe-request-id") if e.headers else None
            if e.code in (429, 529) and i + 1 < tries:  # docs: back off and retry on rate limit / overload
                time.sleep(2 * (i + 1))
                continue
            raise NoDecision(f"TypeSafe answered HTTP {e.code}" + (" (invalid key)" if e.code == 401 else "")
                             + (" (request rejected: see docs/typesafe.md)" if e.code == 422 else "")
                             + (f", request {rid}" if rid else ""))
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            raise NoDecision(f"TypeSafe call failed: {type(e).__name__}")
    raise NoDecision("TypeSafe is busy (429/529)")


def combine(options, answers):
    """Deterministic rule over the two answers (docs: How to build - combine independent answers in code)."""
    opt, cx = answers["option"], answers.get("complexity", {})
    pick, note = opt.get("choice"), None
    o = options.get(pick, {})
    score, cx_conf = float(cx.get("score", 0)), float(cx.get("confidence", 0))
    limit = o.get("max_complexity")
    if limit is not None and score > float(limit) + 0.5 and cx_conf >= LOW:
        target = o.get("escalate_to")
        if target in options:
            note = f"complexity {score:.2f} is above {pick}'s max_complexity {limit}: escalated to {target}"
            pick = target
        else:
            note = f"complexity {score:.2f} is above {pick}'s max_complexity {limit}, and no escalate_to is available"
    return pick, note


def band(conf):
    return "high" if conf >= HIGH else "medium" if conf >= LOW else "low"


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


def route(tid, apply=False, opener=urllib.request.urlopen, today=None):
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
    answers, meta = ask(task_state(t, text), questions(opts, cfg.get("policy", "")), api_key(),
                        cfg.get("jev_model", JEV_MODEL), opener)
    opt, cx = answers["option"], answers.get("complexity", {})
    conf = float(opt.get("confidence", 0))
    probs = sorted(opt.get("probabilities", {}).items(), key=lambda kv: -kv[1])
    margin = probs[0][1] / probs[1][1] if len(probs) > 1 and probs[1][1] else None
    pick, note = combine(opts, answers)
    o = opts.get(pick, {})
    rec = {"task": tid, "at": date.today().isoformat(), **meta, "jevChoice": opt.get("choice"), "choice": pick,
           "confidence": conf, "band": band(conf), "probabilities": dict(probs), "complexity": cx.get("score"),
           "complexityConfidence": cx.get("confidence"), "escalation": note,
           "tool": o.get("tool"), "model": o.get("model"), "effort": o.get("effort"), "applied": False}
    print(f"{tid}: {pick} -> Tool: {o.get('tool')}"
          + (f", Model: {o['model']}" + (f", effort={o['effort']}" if o.get("effort") else "") if o.get("model") else ""))
    print(f"  option confidence {conf:.2f} ({band(conf)})" + (f", top/second {margin:.1f}x" if margin else "")
          + f"; complexity {float(cx.get('score', 0)):.2f} of 2 (confidence {float(cx.get('confidence', 0)):.2f})")
    for k, v in probs[:5]:
        print(f"  {v:.2f}  {k}")
    if note:
        print(f"  {note}")
    u = meta.get("usage") or {}
    print(f"  TypeSafe {meta.get('model')}: {u.get('input_tokens', '?')} in / {u.get('output_tokens', '?')} out tokens,"
          f" request {meta.get('requestId') or '-'}")
    if conf < LOW:
        print("  low confidence: no decision; decide by tool-routing.md")
        _log(tid, rec)
        return NO_DECISION
    if conf < HIGH:
        print("  medium confidence: the Orchestrator confirms before using it (not applied)")
        _log(tid, rec)
        return MEDIUM
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
    return OK


def _log(tid, rec):
    rt = FLOW_ROOT / "tasks" / ".runtime"
    rt.mkdir(parents=True, exist_ok=True)
    p = rt / f"{tid}.route.json"
    hist = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    p.write_text(json.dumps(hist + [rec], indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("id")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        sys.exit(route(a.id, a.apply))
    except (NoDecision, gate.TaskError) as e:
        print(f"{a.id}: no recommendation: {e}. Decide by .agentflow/roles/tool-routing.md")
        sys.exit(NO_DECISION)


if __name__ == "__main__":
    main()
