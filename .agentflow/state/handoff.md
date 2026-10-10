# Session Handoff

## As of

2026-10-10, `main` @ 479c107 - AgentFlow 2.11.0.

## Goal

Keep AgentFlow small while its rules are enforced by the tools, not only written down.

## Verified state

- 2.11.0 merged: `machine_capacity.py` (shared tool slots, PORTs and limit resets across projects on
  one machine), `route.py` effort question (`efforts` / `model_by_effort`), `upstream.py` template
  drift report + `template-source.json` + the conductor's daily check. 93 tests pass.
- Watermark Remover updated to 2.11.0 via `install.py --update`; `upstream.py --check` is clean.

## Assumptions

- Projects run on Windows with PowerShell 7 and Python 3.

## Open problems

- See `state/known-issues.md`.
- `origin/main` is behind; push only on the human's word.

## Files to read first

1. `docs/ai-handoff-protocol.md`
2. `state/current-step.md`
3. `CHANGELOG.md`
