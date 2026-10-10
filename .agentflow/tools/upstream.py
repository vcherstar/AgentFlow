"""Report drift between installed projects and the AgentFlow template, in both directions.

  python .agentflow/tools/upstream.py                       # this project vs its recorded template
  python <template>/.agentflow/tools/upstream.py            # every known project vs this template
  python .agentflow/tools/upstream.py --template <repo> --project <repo> [<repo> ...]
  --check    exit 1 when any project drifts (the conductor's daily check)
  --json     print the report as JSON instead of text

The template is found from --template, then AGENTFLOW_TEMPLATE, then the current project's
`.agentflow/template-source.json` (written by install.py at install and update), then the current
directory itself when it is a template checkout (CHANGELOG.md next to .agentflow/). Projects come
from --project, the machine registry (machine_capacity.py records every project that claimed a
tool slot or was installed on this machine), and the current directory when it holds an installed
.agentflow/.

A difference is a review candidate, not a verdict: a template-owned file that differs while the
project's VERSION matches the template's is an upstream candidate (the project improved the
template); a missing or older file means the project is behind (install.py --update); a file the
project added inside a template-owned directory is reviewed as a new template file or kept
project-owned. Changes that flow upstream are generalized before they enter the template
(protocol, "Installing or updating AgentFlow"); project-owned files are never compared.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install  # noqa: E402
import machine_capacity  # noqa: E402


def template_files(tflow):
    """Relative paths of every template-owned file under a template's .agentflow/."""
    out = set()
    for rel in install.OWNED:
        src = tflow / rel
        if src.is_dir():
            for f in src.rglob("*"):
                if f.is_file() and not (install.SKIP & set(f.relative_to(tflow).parts)):
                    out.add(f.relative_to(tflow).as_posix())
        elif src.exists():
            out.add(rel)
    return out


def version_of(flow):
    f = flow / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "?"


def looks_like_template(root):
    return (root / ".agentflow" / "tools" / "install.py").is_file() and (root / "CHANGELOG.md").is_file()


def source_of(project):
    f = project / ".agentflow" / "template-source.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8-sig")).get("template")
        except (OSError, json.JSONDecodeError):
            return None
    return None


def find_template(explicit, projects, cwd=None):
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("AGENTFLOW_TEMPLATE")
    if env:
        return Path(env).resolve()
    cwd = (cwd or Path.cwd()).resolve()
    if (cwd / ".agentflow" / "template-source.json").exists():
        return Path(source_of(cwd)).resolve()
    if looks_like_template(cwd):
        return cwd
    for project in projects:
        src = source_of(project)
        if src:
            return Path(src).resolve()
    raise SystemExit("STOP: where is the template? pass --template or set AGENTFLOW_TEMPLATE, "
                     "or run inside a project installed by install.py")


def find_projects(explicit, template, cwd=None):
    roots = {Path(p).resolve() for p in explicit}
    cwd = (cwd or Path.cwd()).resolve()
    if (cwd / ".agentflow" / "VERSION").exists():
        roots.add(cwd)
    try:
        roots.update(Path(p).resolve() for p in machine_capacity.known_projects())
    except OSError:
        pass
    return sorted(p for p in roots if (p / ".agentflow" / "VERSION").exists() and p != template)


def same_file(a, b):
    """Same content once CRLF/LF is normalized: checkouts differ across machines and installs."""
    return a.read_bytes().replace(b"\r\n", b"\n") == b.read_bytes().replace(b"\r\n", b"\n")


def compare(tflow, pflow):
    """(differs, missing, added) template-owned files of one project against the template."""
    tfiles = template_files(tflow)
    differs, missing = [], []
    for rel in sorted(tfiles):
        dst = pflow / rel
        if not dst.exists():
            missing.append(rel)
        elif not same_file(dst, tflow / rel):
            differs.append(rel)
    added = []
    for owned_dir in install.OWNED_DIRS:
        d = pflow / owned_dir
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file():
                rel = f.relative_to(pflow).as_posix()
                if rel not in tfiles and not (install.SKIP & set(f.relative_to(pflow).parts)):
                    added.append(rel)
    return differs, missing, added


def drifted(entry):
    return bool(entry["behind"] or entry["differs"] or entry["missing"] or entry["added"])


def report(template, projects):
    tflow = template / ".agentflow"
    if not (tflow / "VERSION").exists():
        raise SystemExit(f"STOP: {tflow} holds no installed AgentFlow (no VERSION)")
    tv = version_of(tflow)
    entries = []
    for p in projects:
        pflow = p / ".agentflow"
        pv = version_of(pflow)
        differs, missing, added = compare(tflow, pflow)
        entries.append({"project": str(p), "version": pv, "behind": pv != tv,
                        "differs": differs, "missing": missing, "added": added})
    return {"template": str(template), "version": tv, "projects": entries}


def print_report(rep):
    print(f"template: {rep['template']} (AgentFlow {rep['version']})")
    if not rep["projects"]:
        print("\nno installed projects found (--project, the machine registry, or the current directory)")
    for e in rep["projects"]:
        head = f"{e['project']} (AgentFlow {e['version']}"
        head += f", behind {rep['version']}: install.py --update)" if e["behind"] else ")"
        print(f"\n{head}")
        if not drifted(e):
            print("  clean: no drift")
        for rel in e["differs"]:
            hint = "same version: review for upstream merge" if not e["behind"] else "probably just behind; worth a look"
            print(f"  differs: .agentflow/{rel} ({hint})")
        for rel in e["missing"]:
            print(f"  missing in project: .agentflow/{rel}")
        for rel in e["added"]:
            print(f"  added inside template-owned dir: .agentflow/{rel} (port upstream or keep project-owned)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--template", type=Path, help="the AgentFlow template checkout")
    ap.add_argument("--project", type=Path, action="append", default=[], help="a project to check (repeatable)")
    ap.add_argument("--check", action="store_true", help="exit 1 when any project drifts")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args()
    candidates = [Path(p).resolve() for p in a.project]
    template = find_template(a.template, candidates)
    projects = find_projects(a.project, template)
    rep = report(template, projects)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print_report(rep)
    return 1 if a.check and any(drifted(e) for e in rep["projects"]) else 0


if __name__ == "__main__":
    sys.exit(main())
