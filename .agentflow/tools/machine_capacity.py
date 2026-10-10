"""Coordinate AgentFlow sessions across projects on one machine.

The registry is shared by all projects of the current OS user. A short OS file lock
serializes claims, so two launchers cannot both take the last tool slot or PORT.
Leases follow worker PIDs and are removed after normal exit or when the process dies.
Only capacity and temporary tool unavailability live here; task memory stays local.
"""
import argparse
import ctypes
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

TOOLS = ("codex", "claude", "devin", "agy")
DEFAULT_CAPACITY = {"codex": 1}
PENDING_SECONDS = 600


def state_dir():
    override = os.environ.get("AGENTFLOW_MACHINE_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    return Path(base).expanduser().resolve() / "AgentFlow" / "machine" if base else Path.home() / ".agentflow-machine"


def _alive(pid, started=None):
    if not pid:
        return False
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                return False
            if started:
                times = [ctypes.c_ulonglong() for _ in range(4)]
                if kernel.GetProcessTimes(handle, *(ctypes.byref(x) for x in times)):
                    return times[0].value + 504911232000000000 == int(started)
            return True
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


@contextmanager
def _locked():
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "registry.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            for _ in range(100):
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise TimeoutError("machine capacity registry is busy")
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            path = directory / "registry.json"
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                state = {"leases": [], "limits": {}}
            state.setdefault("leases", [])
            state.setdefault("limits", {})
            state.setdefault("projects", {})
            yield state
            temp = directory / f"registry.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(temp, path)
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _clean(state):
    now = time.time()
    state["leases"] = [lease for lease in state["leases"] if
                       (_alive(lease.get("pid"), lease.get("pidStart")) if lease.get("active") else
                        now - lease.get("created", 0) < PENDING_SECONDS or
                        _alive(lease.get("pid"), lease.get("pidStart")))]
    state["limits"] = {tool: end for tool, end in state["limits"].items() if end > now}
    state["projects"] = {p: t for p, t in state.get("projects", {}).items()
                         if Path(p).is_dir() or now - t < PENDING_SECONDS}


def capacity(tool, project=None):
    if tool not in TOOLS:
        raise ValueError(f"unknown tool: {tool}")
    raw = os.environ.get(f"AGENTFLOW_MACHINE_{tool.upper()}_MAX")
    value = DEFAULT_CAPACITY.get(tool, 0) if raw is None else int(raw)
    if value < 0:
        raise ValueError("machine capacity must be zero or positive")
    if project:
        root = Path(project)
        rules = (root / ".agentflow" / "docs" / "engineering-rules.md",
                 root / ".agentflow" / "docs" / "project-rules.md", root / "AGENTS.md")
        for path in rules:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                marker = f"- parallel: {tool}="
                if line.strip().startswith(marker):
                    project_value = int(line.strip()[len(marker):].strip("` "))
                    if project_value < 1:
                        raise ValueError("project parallel capacity must be positive")
                    value = min(value, project_value) if value else project_value
    return value


def has_capacity(tool, project, at=None):
    """A read-only scheduling hint; claim() is still the atomic final decision."""
    requested = capacity(tool, project)
    with _locked() as state:
        _clean(state)
        if state["limits"].get(tool, 0) > (at.timestamp() if at else time.time()):
            return False
        current = [lease for lease in state["leases"] if lease["tool"] == tool]
        active_caps = [lease["cap"] for lease in current if lease.get("cap")]
        effective = min([x for x in [requested, *active_caps] if x], default=0)
        return not effective or len(current) < effective


def claim(tool, project, owner, port=None, cap=None, pid=None, pid_start=None):
    requested = capacity(tool, project) if cap is None else cap
    if requested < 0:
        raise ValueError("machine capacity must be zero or positive")
    with _locked() as state:
        _clean(state)
        if state["limits"].get(tool, 0) > time.time():
            return None, f"{tool} is globally limited until {datetime.fromtimestamp(state['limits'][tool], timezone.utc).isoformat()}"
        current = [lease for lease in state["leases"] if lease["tool"] == tool]
        active_caps = [lease["cap"] for lease in current if lease.get("cap")]
        effective = min([x for x in [requested, *active_caps] if x], default=0)
        if effective and len(current) >= effective:
            return None, f"{tool} has no free machine slot ({len(current)}/{effective})"
        if port and any(str(lease.get("port")) == str(port) for lease in state["leases"]):
            return None, f"PORT={port} is reserved by another AgentFlow project"
        token = uuid.uuid4().hex
        state["projects"][str(Path(project).resolve())] = time.time()
        state["leases"].append({"token": token, "tool": tool, "project": str(Path(project).resolve()),
                                "owner": owner, "port": str(port) if port else None, "cap": requested,
                                "pid": int(pid or os.getpid()), "pidStart": int(pid_start) if pid_start else None,
                                "active": False, "created": time.time()})
        return token, ""


def activate(token, pid, pid_start=None):
    with _locked() as state:
        for lease in state["leases"]:
            if lease["token"] == token:
                lease.update(pid=int(pid), pidStart=int(pid_start) if pid_start else None, active=True)
                return True
    return False


def release(token):
    with _locked() as state:
        state["leases"] = [lease for lease in state["leases"] if lease["token"] != token]


def record_limit(tool, until):
    with _locked() as state:
        state["limits"][tool] = until.timestamp()


def limit_until(tool):
    with _locked() as state:
        _clean(state)
        end = state["limits"].get(tool)
    return datetime.fromtimestamp(end, timezone.utc) if end else None


def known_projects():
    """Project roots that claimed a slot or were installed here (existing AgentFlow installs only)."""
    with _locked() as state:
        projects = dict(state.get("projects", {}))
    return sorted(p for p in projects if (Path(p) / ".agentflow").is_dir())


def register_project(project):
    """Remember a project root without claiming a slot (install.py calls this)."""
    with _locked() as state:
        _clean(state)
        state["projects"][str(Path(project).resolve())] = time.time()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    take = sub.add_parser("claim")
    take.add_argument("tool", choices=TOOLS)
    take.add_argument("project")
    take.add_argument("owner")
    take.add_argument("--port")
    take.add_argument("--max", type=int, dest="cap")
    take.add_argument("--pid", type=int)
    take.add_argument("--pid-start", type=int)
    active = sub.add_parser("activate")
    active.add_argument("token")
    active.add_argument("pid", type=int)
    active.add_argument("--pid-start", type=int)
    sub.add_parser("status")
    sub.add_parser("projects")
    done = sub.add_parser("release")
    done.add_argument("token")
    args = parser.parse_args()
    try:
        if args.command == "claim":
            token, reason = claim(args.tool, args.project, args.owner, args.port, args.cap, args.pid, args.pid_start)
            if token:
                print(token)
            else:
                print(reason, file=sys.stderr)
                return 4  # busy: wait for a shared resource, do not create an Orchestrator need
        elif args.command == "activate":
            return 0 if activate(args.token, args.pid, args.pid_start) else 1
        elif args.command == "release":
            release(args.token)
        elif args.command == "status":
            with _locked() as state:
                _clean(state)
                print(json.dumps(state, indent=2))
        elif args.command == "projects":
            for project in known_projects():
                print(project)
    except (OSError, ValueError, TimeoutError) as error:
        print(f"machine capacity: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
