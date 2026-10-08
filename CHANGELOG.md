# Changelog

What changed in each AgentFlow version. Why it changed: `.agentflow/state/decisions.md`. The version of an installed project: `.agentflow/VERSION` (2.1.x: `AgentFlow version:` in its `AGENTS.md`).

## 2.6.0 - 2026-10-08

Orchestration survives the end of an Orchestrator session (usage limit, closed window):

- New `.agentflow/tools/tick.py`: `run` takes the steps that need no judgment (accept a Tester with `Verdict: pass`, accept a Developer whose Tester is done or whose check is `none`, launch ready tasks whose dependencies are done and whose tool is not limited) through `accept.py` and `run-task.ps1`, and lists the rest as needs in `.runtime/tick.json`; `--dry-run` changes nothing. `heartbeat` / `release` keep one Orchestrator at a time (a heartbeat older than 20 minutes is not live; while one is live, `run` only reports). `limit` and `tool-limits.json` remember when a limited tool is usable again, learned from attempt logs ("resets 3:30pm", "resets in 158h43m"; one hour otherwise). `next-orchestrator` picks the first free tool of `AGENTFLOW_ORCHESTRATORS` (default `claude,codex,devin,agy`).
- Protocol section "Autonomous orchestration": what a background Orchestrator may do (the approved plan only: launch, accept, reject, recovery, successors, Tester tasks) and may never do (new scope, plan or rule changes, push, publish, deploy, production); questions for the human go to the new `.agentflow/state/questions.md`.
- Orchestrator role: heartbeat after every step; on its own limit it records it and releases.
- `test_tick.py`: decisions, acceptance chains in one tick, refused acceptance, limits and reset times, heartbeat ownership.

Migration from 2.5.x: `install.py --update` (creates `questions.md`).

## 2.5.3 - 2026-10-08

- Fix: `accept.py` refused every acceptance once the committed memory (for example the ledger) had changed: the trimmed `git status` output lost the first line leading space, so `.agentflow/...` read as `agentflow/...` and counted as a change outside the memory. It now reads `git status --porcelain -z` raw. Tests commit the memory before acceptance, as a real main folder does, and check that a real change outside it still blocks.

Migration from 2.5.x: `install.py --update`.

## 2.5.2 - 2026-10-08

- Fix: tester tasks in a single-repository project could not launch since 2.3.0. The task JSON for `run-task.ps1` carried the single repository commit under an empty key, which PowerShell `ConvertFrom-Json` rejects; `gate.py` now writes that key as `.`. A new test reads a tester task JSON with real PowerShell.

Migration from 2.5.x: `install.py --update`.

## 2.5.1 - 2026-10-08

- Fix: a task `Model:` line now replaces the matching flags from `AGENTFLOW_<TOOL>_ARGS` instead of adding a second copy. Devin exited at start ("--model cannot be used multiple times") when both were set, and for Codex a machine `-c model_reasoning_effort` silently overrode the task effort.
- New `test_launcher_args.py` runs the launcher functions in real PowerShell.

Migration from 2.5.0: `install.py --update`.

## 2.5.0 - 2026-10-08

TypeSafe integration rebuilt from the official documentation (https://docs.typesafe.ai):

- New template-owned `.agentflow/docs/typesafe.md`: links to every relevant documentation page and the rules
  `route.py` follows; the protocol and tool routing point to it.
- `route.py`: state is an object with named fields, clipped well under the 32k-token budget; one request asks two
  atomic questions in parallel - choice "option" and 3-level score "complexity" - combined in code (a pick whose
  `max_complexity` is exceeded escalates to its `escalate_to`); choice criteria are objects (`what`, `not_for`,
  `examples`; the old `describe` still works); the policy goes into the instructions; confidence bands from the docs
  (>= 0.9 apply, 0.5-0.9 confirm - exit 4, < 0.5 no decision - exit 3; `--threshold` removed); Jev pinned to
  `jev-1.13.0` (`jev_model` in the options file); `TYPESAFE_BASE_URL` (the SDK's name; `TYPESAFE_API_BASE` is gone);
  the request id also on errors.
- Tests: the stand-in server checks the documented request shape; tests clear every `TYPESAFE_*` variable so they can
  never reach the real service.

Migration from 2.4.x: `install.py --update`; in `model-options.json` rename `describe` to `what` (optional), add
`not_for` / `examples`, and `max_complexity` / `escalate_to` where a stronger option should take hard tasks.

## 2.4.1 - 2026-10-08

- route.py prints and logs the TypeSafe usage (input and output tokens) and the request id (`x-typesafe-request-id`) of every call, so a call can be matched with the TypeSafe dashboard or support.

Migration from 2.4.0: `install.py --update`.

## 2.4.0 - 2026-10-08

- Task File line `Model: <model>[, effort=<level>]`: the launcher adds the tool's flags (codex `-m` and `-c model_reasoning_effort`, agy `--model` / `--effort`, claude and devin `--model`). New machine variables `AGENTFLOW_CLAUDE_ARGS`, `AGENTFLOW_AGY_ARGS`, `AGENTFLOW_DEVIN_ARGS`.
- New `.agentflow/tools/route.py`: recommends the tool and model for a task with TypeSafe AI (Jev, one choice question over the project's options) and, with `--apply`, writes `Tool:` / `Model:` into a task that has not started. Options and a policy in words live in the new project-owned `.agentflow/docs/model-options.json` (with `from` / `until` windows and roles); a tester never gets the developer's tool while another option remains. The key stays outside the repository (`TYPESAFE_API_KEY` or `AGENTFLOW_TYPESAFE_KEY_FILE`) and is never printed. No key, an API error or low confidence: exit 3, decide by Tool Routing.
- Tests: `test_route.py` against a local stand-in for the API.

Migration from 2.3.0: `install.py --update` (it creates the `model-options.json` stub); fill the options and set the key variable to use `route.py`.

## 2.3.0 - 2026-10-08

- Workspace mode: a folder that holds several repositories. Its root becomes a small memory repository (it tracks only AgentFlow files and the root entry points; the product repositories are ignored by it and stay independent); `.agentflow/workspace.json` lists the repositories. Task Files name them in `Repo:`; paths start with the repository folder; commits are `<repo>@<SHA>, ...` in `Change:`, `Verifies:`, `Deploys:`. One branch per task in every named repository; `Worktree` holds one worktree per repository.
- `gate.py`: repositories per task, per-repository branch / worktree / diff / merge checks, tester checkouts per repository (`checkouts` in the task JSON drive `run-task.ps1`). Single-repository projects behave as before.
- `accept.py`: a trial merge (`git merge-tree`) in every repository before any merge; untracked files no longer block acceptance; the workspace memory repository does not count its product repositories as changes.
- `run-task.ps1`: creates and removes worktrees and tester checkouts per repository; a developer worktree is re-created on an existing task branch.
- `install.py`: `--workspace`, `--repos`, `--git-init`; finds the repositories by their `.git`; points to the workspace option when a folder of repositories is refused.
- Tests: `test_workspace.py` runs the real tools end to end on throwaway repositories (single repository, two repositories, a file outside Allowed files, a conflict in one repository, tester and Repo validation).

Migration from 2.2.0: `install.py --update`. Projects with one repository change nothing.

## 2.2.0 - 2026-10-07

Lessons from running the Watermark Remover project in Team Mode with Claude Code, Codex, Devin and Antigravity.

Layout and installation:

- Everything AgentFlow owns lives in `.agentflow/` (protocol, roles, commands, Task File template, tools, dashboard, tests, VERSION, README). The project root keeps only `AGENTS.md`, `CLAUDE.md`, `.claude/commands/` pointers and a `.gitignore` block, so AgentFlow no longer collides with a project's own `docs/`, `tasks/` or `tools/`. Tools find both roots through `.agentflow/tools/paths.py`; the dashboard still reads ledger and Task File history from before the move.
- New `.agentflow/tools/install.py`: installs into an existing repository or updates it (`--update`, `--dry-run`, `--inside-repo`). It deletes nothing, adds a marked block to existing `AGENTS.md`, `CLAUDE.md` and `.gitignore` instead of rewriting them, keeps foreign `.claude/commands/` files and project-added files in `.agentflow/`, creates missing project-owned stubs, refuses a non-root or non-git target, an existing install without `--update`, a 2.1 root layout, and an update over uncommitted template-owned changes.
- Version: `.agentflow/VERSION`. Project rules: `.agentflow/docs/project-rules.md` (also `engineering-rules.md`, and rules in `AGENTS.md` outside the AgentFlow block).

Tools:

- New `.agentflow/tools/accept.py`: verify -> merge `--no-ff` -> ledger `done` -> worktree remove -> `branch -d`, stopping at the first failure and aborting a failed merge. Testers: verify and ledger only.
- `gate.py`: Task File fields are read from the header only (a report line `Target: local` broke the end check); a short `Change:` SHA is expanded to the full one.
- `run-task.ps1`: `devin` tool (`-p --permission-mode dangerous --respect-workspace-trust false`); `agy` developers run in print mode with `--output-format stream-json` (no manual `/exit`); `AGENTFLOW_CLAUDE`, `AGENTFLOW_AGY`, `AGENTFLOW_DEVIN`, `AGENTFLOW_PYTHON` overrides; `python` falls back to `py -3` when it is the Microsoft Store stub; `AGENTFLOW_*` and `PATH` are handed to the worker window through `tasks/.runtime/T-NNN.env.json` (a Store-packaged pwsh does not pass its environment); QuickEdit is switched off in worker windows (a click paused them); "session limit" and "hit your limit" count as usage limits.
- New tests: `test_layout.py`, `test_install.py`, `test_accept.py`.

Protocol and roles:

- Result rules: the Result is everything after the last `## Result` line; nothing above it changes, blank lines included; headers never repeat that heading text; no report line starts with a header field name.
- New "Successors and re-tests": merge-only successor for a branch that predates a fix on main; re-tests reuse earlier evidence; a same-tool Tester only when no other tool is free.
- Launching workers: machine setup (environment handover, Python, QuickEdit) and the Claude Code permission the human must grant for the Orchestrator to launch workers.
- Orchestrator: foundation task first (shared files and contracts), one owner per shared file, an early preview build for the human, acceptance through `accept.py`, no `;`-chained state changes.
- Developer: commit early and amend the single task commit on long tasks. Tester: report placement rules; mark checks blocked by the environment `unverified`.
- Tool routing: Devin, plan for limits (Claude worker sessions can end in minutes, Antigravity quotas for days, the Codex sandbox on Windows can refuse processes), the Codex desktop-app CLI path.
- GUIDE: installation into an existing project, update, migration from the root layout, machine setup.

Migration from 2.1.x (root layout):

1. In a branch, `git mv` the AgentFlow folders into `.agentflow/` (`docs/ai-handoff-protocol.md`, `docs/project-plan.md`, `roles/`, `state/`, `tasks/`, `tools/`, `dashboard/`; `.claude/commands/*.md` -> `.agentflow/commands/`); keep project files that only share a folder name; move `docs/engineering-rules.md` too if it holds Project rules.
2. `python <template>/.agentflow/tools/install.py <project> --update --dry-run`, then without `--dry-run`.
3. Delete by hand the old 2.1 template text in `AGENTS.md` above `## Project rules` and the old slash-command list in `CLAUDE.md` (the installer never deletes); keep the project's rules.
4. Open Task Files: paths `tasks/`, `state/`, `docs/` -> `.agentflow/...`; finish running attempts before the move (their runtime state moves with `tasks/`).

Migration from a hand-made `.agentflow/` adaptation of 2.1.0: commit everything, then `install.py --update`; files the project added (wrappers, notes) are kept and listed.

## 2.1.0 - 2026-10-04

- New template-owned `dashboard/`: a read-only view for the human built from the ledger, Task Files, and git (`python dashboard/build.py` -> `dashboard/out/index.html`, `out/graph.html`). Task table with filters over every axis, task card, Gantt, timeline, dependency, successor and check links. Interface in Russian (it is human-facing); ledger statuses are shown as Russian labels.
- `dashboard/snapshot.py`: local versions of the dashboard sources with rollback; `dashboard/UI-RULES.md`: rules for changing the interface; `dashboard/README.md`: files and the data contract.
- Protocol: new section "Dashboard"; `dashboard/` added to the template-owned list. `.gitignore` gets `dashboard/out/` and `dashboard/versions/`.
- Orchestrator role: rebuild the dashboard when the human asks or a Stage closes.

Migration from 2.0.x: copy `dashboard/`, append the two `.gitignore` lines, replace `docs/ai-handoff-protocol.md` and `roles/orchestrator.md`. No Task File or ledger changes.

## 2.0.0 - 2026-10-03

- Tester review isolation: a disposable checkout of the `Verifies` commit; the launcher fails the attempt if the checked branch, worktree, or Task File changed. Codex is the first choice for Tester tasks.
- Every attempt goes through `tools/run-task.ps1`; `-Manual` gates and prepares sessions a human starts (Antigravity IDE, Deployer, live Tester on prod).
- Production approval: the human gives it in the Deployer session; the Deployer records `Approval: source=human target=prod sha=<SHA> at=<time>`.
- New `tools/gate.py`: preflight, end-of-attempt check, `verify` (acceptance evidence), `stage` (Stage check on the main branch).
- Attempt history in `tasks/.runtime/T-NNN.json`, one log per attempt; global launch lock.
- State families: Task state, Process state, Outcome, role result (Change / Verdict / Deployment), Stage state. Acceptance is the ledger transition `review -> done`, allowed only after a passing verify.
- `tools/ledger.py`: enforced transitions, `Updated` column, `done` needs a passing verify, `rejected` / `cancelled` need notes.
- Template and project separated: ownership list and install / update procedure in the protocol; the template's own `state/` and `docs/project-plan.md` are not copied; guide moved to `GUIDE.md`.
- Machine-facing instructions in English; human communication in Russian.

Migration from 1.x:

- Task File fields: `Checks: T-xxx, commit <SHA>` -> `Verifies: T-xxx @ <SHA>`; `Environment:` -> `Target:`; deployer `Deploys: <SHA>`; `Prod approved by human:` removed; section `## Environment setup` -> `## Setup`; section `## Review before merge` -> field `Independent check: tester | none - <reason>`.
- Result: `Status:` -> `Outcome: completed | blocked | failed` plus `Change:` (developer), `Verdict:` and `Criteria:` (tester), `Deployment:` (deployer).
- Ledger: `rework` -> `rejected` (successor in Notes); the `Updated` column is added automatically.
- Process states: `completed` -> `exited`, `failed` -> `error`.
- Stage status: `done` -> `closed`, `pending` -> `planned`; `Result:` -> `Exit criteria:`.
- Finish or re-issue open tasks after updating: a running 1.x attempt has no attempt history.

## 1.2.0 - 2026-10-02

- Launch preflight with project `## Preflight` deny / require rules, `## Checks` section, production opt-in through `AGENTFLOW_TARGET`.

## 1.1.0 - 2026-10-02

- Launcher and ledger defects found migrating a live project fixed.

## 1.0.0 - 2026-10-01

- Team layer: four roles, Task Files, Task Ledger, `/start-role`, tool routing, `tools/run-task.ps1`, `tools/ledger.py`, on top of the base AI Project Memory (2026-05-21).
