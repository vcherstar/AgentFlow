# Session Log

Chronological log of work on the AgentFlow template.

## 2026-05-21

- Base AI Project Memory: protocol, handoff, current-step, decisions, known-issues, session-log.

## 2026-10-01

- Team layer: roles, Task Files, Task Ledger, `/start-role`, protocol sections "Roles and memory ownership", "Starting a role session", "Task lifecycle", "Planning levels", "Git rules", `AGENTS.md`, `roles/tool-routing.md`.

## 2026-10-02

- Launcher and ledger defects from migrating a live project fixed; preflight, `## Checks`, production opt-in added.

## 2026-10-03

- FPF audit of all files; refactor to 2.0.0 in five commits: P0 enforcement, P1 state and evidence, P2 rule order and contradictions, P3 template vs project, P4 English machine-facing text. Details: `CHANGELOG.md`, `state/decisions.md`.

## 2026-10-07: 2.2.0

- Moved the template into `.agentflow/` with `git mv`; ported the Watermark Remover adaptation (`paths.py`, relocated
  paths, dashboard history and links, layout tests) and its fixes; added `install.py`, `accept.py`, VERSION, tests.
- Protocol, roles, tool routing, Task File template, README, GUIDE, CHANGELOG updated. 19 tests pass.


## 2026-10-10: Launcher reliability

Implemented 2.11.3 launcher, end-check and acceptance repairs with regression tests in disposable repositories; deployment to the pilot follows the installer workflow.

Final validation: 105/105 tests pass (including PowerShell 5.1/7 and fake-worker
tick launch). Pilot full suite: 105/105; focused lifecycle rerun: 7/7. Pilot
update committed as 66deac8 on master; no template drift or product changes.
Conductor restarted hidden and reports zero needs/waits, with autostart enabled.
A test-only temporary-directory cleanup race was fixed by waiting for worker exit.
