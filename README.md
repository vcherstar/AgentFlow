# AgentFlow

Project memory and four agent roles for running one software project with several AI coding sessions (Claude Code, Codex CLI, Antigravity, Devin) without losing state or overwriting each other's work.

- **Orchestrator**: one session that splits the goal into tasks, launches workers, accepts results on evidence, and is the only writer of project memory.
- **Developer, Tester, Deployer**: fresh sessions, one task each, started through `.agentflow/tools/run-task.ps1`.

All rules live in one file, [.agentflow/docs/ai-handoff-protocol.md](.agentflow/docs/ai-handoff-protocol.md). Everything else points there.

Everything AgentFlow puts into a project lives in one folder, `.agentflow/`, so it never collides with the project's own `docs/`, `tasks/` or `tools/`. The project root gets only small entry points (`AGENTS.md`, `CLAUDE.md`, `.claude/commands/`) and a marked block in `.gitignore`; existing files keep their content.

## Contents

| Path | What |
|---|---|
| `.agentflow/docs/ai-handoff-protocol.md` | the protocol: terms, states, rules, session steps |
| `.agentflow/roles/` | one instruction file per role; `tool-routing.md`: which tool takes which task |
| `.agentflow/commands/` | slash-command texts (`.claude/commands/` holds one-line pointers) |
| `.agentflow/tasks/_template.md` | Task File template |
| `.agentflow/tools/` | `install.py` installer and updater, `run-task.ps1` launcher, `gate.py` preflight / verify / Stage check, `accept.py` acceptance (verify, merge, ledger, cleanup), `ledger.py` Task Ledger |
| `.agentflow/dashboard/` | read-only view for the human: task table, filters, Gantt, timeline (`python .agentflow/dashboard/build.py`) |
| `.agentflow/tests/` | tests of the tools (`python -m unittest discover -s .agentflow/tests`) |
| `.agentflow/VERSION`, `.agentflow/README.md` | version and folder map |
| `AGENTS.md`, `CLAUDE.md`, `.claude/commands/` | entry points for the tools |
| `GUIDE.md` | step-by-step guide (Russian): install, update, machine setup, daily use |
| `CHANGELOG.md` | what changed in each version |

The template's own memory (`.agentflow/state/`, `.agentflow/docs/project-plan.md`) describes the development of AgentFlow itself and is never copied into a project.

Requirements: git, Python 3, PowerShell 7 on Windows, and the CLIs of the tools you use.

## Start

```powershell
python <AgentFlow>\.agentflow\tools\install.py <project> --dry-run   # see what would change
python <AgentFlow>\.agentflow\tools\install.py <project>             # install (later: --update)
```

Then follow [GUIDE.md](GUIDE.md). In Claude Code the Orchestrator starts with `/start-role orchestrator` and your goal; a session without a role uses `/start-session`.

License: MIT.
