# .agentflow

Everything AgentFlow needs lives in this folder, so it never collides with a project's own `docs/`, `tasks/`,
`tools/` or `state/`. Git and product commands still run from the repository root.

| Path | Owner | What |
|---|---|---|
| `VERSION` | template | installed AgentFlow version |
| `docs/ai-handoff-protocol.md` | template | the protocol: terms, states, rules, session steps |
| `docs/project-rules.md`, `docs/project-plan.md` | project | Project rules (worktrees, Preflight, Tool routing) and the roadmap |
| `roles/` | template | one file per role; `tool-routing.md`: which tool takes which task |
| `commands/` | template | slash-command texts; `.claude/commands/` holds one-line pointers here |
| `tasks/_template.md` | template | Task File template; `tasks/T-NNN-*.md` are the project's Task Files |
| `tasks/.runtime/` | local | attempt history, logs, evidence (git-ignored) |
| `state/` | project | handoff, current step, decisions, known issues, session log, Task Ledger |
| `workspace.json` | project | workspace only: the product repositories under the workspace root |
| `tools/` | template | `run-task.ps1` launcher, `gate.py`, `ledger.py`, `accept.py`, `install.py`, `paths.py` |
| `dashboard/` | template | read-only view for the human (`python .agentflow/dashboard/build.py`) |
| `tests/` | template | checks of the tools themselves (`python -m unittest discover -s .agentflow/tests`) |

Template-owned files are replaced by `install.py --update`; project-owned files are never overwritten.
Rules: `docs/ai-handoff-protocol.md`, section "Installing or updating AgentFlow".
