# Decision Log

Decisions about the AgentFlow template itself: why, and what was rejected. What changed per version: `CHANGELOG.md`. Add new decisions below; do not delete old ones without a reason.

## 2026-05-21

### AI Project Memory

Decision: baseline memory files: protocol, handoff, current-step, decisions, known-issues, session-log.

Why: long AI sessions lose context, repeat old errors, and forget why decisions were made. A short handoff is the entry point; details live in specialized files.

## 2026-10-04

### Dashboard is template-owned, in `dashboard/` beside `tools/`

Decision: the human's read-only view (built from the ledger, Task Files, git) ships with the template as `dashboard/`, Russian interface (it is for the human; machine-facing files stay English), statuses mapped from the protocol's ledger words. Only sources are committed; `out/` and `versions/` are git-ignored. Local versioning (`snapshot.py`) stays so the dashboard can be changed and rolled back.

Why: it was crystallized on one real project and the entities and flow are the same in every AgentFlow project. `tools/` is for agents; this is for the human, so a separate folder. Rejected: shipping built pages (they are project data, not template).

## 2026-10-01

### Team roles and single memory writer

Decision: four roles, Task Files in `tasks/`, Task Ledger `state/tasks.md`. In Team Mode only the Orchestrator writes Canonical Memory; workers write only their Result.

Why: several sessions updating handoff and current-step in parallel create conflicting versions of project state. Workers need their role and task, not the whole history.

### One task = one branch + one worktree, mandatory cleanup

Decision: each developer task gets branch `t-NNN-slug` and a worktree outside the repository and cloud sync; the main folder stays on the main branch and belongs to the Orchestrator; after merge the Orchestrator removes the worktree and the branch.

Why: on an earlier project 36 of 37 task branches with worktrees merged; the real problem was 33 merged worktrees left behind. Rejected: a no-worktree rule (loses parallel work).

### Tool routing by fit and budget

Decision: the Orchestrator picks the tool per task from `roles/tool-routing.md` and the limits the human reports; the choice is written in the Task File and ledger.

Why: tools differ in strengths and token cost; spending the most capable tool on mechanical work wastes limits.

## 2026-10-03

Context: an audit against the First Principles Framework (FPF) found rules that the scripts did not enforce, overloaded status words, evidence that was overwritten, and template history mixed into project memory. FPF was used as a reference, not as a target: a principle was applied only where it fixed a concrete defect.

### Enforcement before prose

Decision: prefer code enforcement, then structured fields, then one canonical rule; no rule repeated across files.

Why: every audit defect of the form "the text says X, the tool does Y" came from a rule that existed only as prose. Rejected: more prompt instructions (more tokens, same gap).

### Review isolation instead of "read-only Tester"

Decision: the Tester works in a disposable checkout of the checked commit; the launcher compares the checked branch, worktree, and Task File before and after the attempt. Codex (sandboxed) is the first choice; Claude is a fallback guarded only by the end check.

Why: the Tester needs write access for test runs and its Result, so "read-only" could not be true; what matters is that it cannot change the artifact that is then accepted. Rejected: Tester only on Codex (no fallback when its limit ends).

### Production approval stays in the Deployer session

Decision: the Deployer asks the human in its own designated session and records `source=human target sha at`; the Orchestrator is not in the chain.

Why: an approval relayed by the Orchestrator was a record it could write itself. Rejected: an `approve.ps1` record file (an agent with a shell can write the file as well).

### Acceptance as an event, separate state families

Decision: one owner and one vocabulary per family (Task state, Process state, Outcome, role result, Stage state); acceptance is the ledger transition `review -> done`, allowed by `ledger.py` only after a passing `gate.py verify`. No separate "Decision" status.

Why: `done`, `failed`, `blocked` meant different things in five places. Rejected: a common `Claim` result for all roles (Developer, Tester, and Deployer produce different results).

### Pure logic in Python, side effects in PowerShell

Decision: `tools/gate.py` parses Task Files and runs preflight, end check, verify, and the Stage check; `tools/run-task.ps1` creates worktrees and checkouts and runs processes.

Why: with verify and attempt history the launcher would have become parser, verifier, state machine, and evidence store at once; Python logic is easier to test and is shared with `ledger.py`.

### Template-owned vs project-owned files

Decision: ownership list and install / update procedure in the protocol; `AgentFlow version` in `AGENTS.md`; the template's own `state/` and `docs/project-plan.md` are never copied; project tool notes live in Project rules.

Why: projects inherited template history as their own decisions, and updating `roles/` overwrote project edits in `tool-routing.md`.

### Language

Decision: machine-facing files in English; communication with the human in Russian, generated from the canonical rule, never stored as a second copy.

Why: Russian text costs about twice the tokens of English, and two copies of a rule drift apart.

## 2026-10-07: 2.2.0 - one `.agentflow/` folder, installer, lessons from a real Team Mode run

Why: installing 2.1 into an existing workspace collided with its own `tasks/`, `tools/`, `AGENTS.md`; the Watermark Remover
project ran Team Mode for two days with four tools and surfaced template defects (gate parsing the whole Task File,
undetected Claude session limits, Store-packaged pwsh not passing the environment, the Python Store stub, QuickEdit
pausing workers, manual multi-step acceptance going wrong) and missing guidance (Devin, Antigravity print mode, Codex
desktop-app path, limit-aware routing, merge-only successors, re-tests reusing evidence).
Decided: move everything into `.agentflow/` (the layout Watermark Remover adapted by hand), add `install.py` (marked
blocks, no deletion, dry run) and `accept.py` (stop at the first failure), and fold the fixes into the protocol, roles
and tools. Rejected: keeping the root layout (collisions); rewriting existing entry files (loses project rules);
supporting multi-repo workspaces now (tools assume one repository; recorded as a known limit).

## 2026-10-08: 2.3.0 - workspace mode

Why: the human's AIHomeDesign workspace holds five repositories and is not a repository itself; the human asked for
both a shared mode and the per-repository install. Decided: the workspace root becomes a memory repository that
tracks only AgentFlow files (default-ignore everything else, product repositories ignored); `workspace.json` lists the
repositories; tasks name them in `Repo:`; paths are workspace-relative so Checks read the same in the root and in a
task folder; acceptance does a trial merge everywhere first. Rejected: memory without git (no history, no dashboard);
git submodules (they change the product repositories); per-repository memory copies (two sources of truth).

## 2026-10-08: 2.4.0 - per-task model and TypeSafe routing

Why: the human pays for several tools with different free windows (Devin SWE-2 free until 2026-10-16, unused Codex
capacity) and asked to use TypeSafe AI to pick the model per task. Decided: a `Model:` line per task turned into tool
flags by the launcher; the project lists its options and a policy in `model-options.json`; `route.py` asks Jev one
choice question and only advises (threshold, exit 3 otherwise; `--apply` only before launch). Rejected: letting the
service choose silently at launch (the Orchestrator must see and own the choice); storing the key in the repository.

## 2026-10-08: 2.5.0 - TypeSafe by its official documentation (human)

The human asked to use the official TypeSafe documentation whenever TypeSafe is used. The 2.4 integration had been
built from the API reference, one primitive page and a third-party summary, and differed from the docs (text state,
one broad question, one 0.5 threshold, `jev-latest`, a proxy's variable name). Rebuilt to the docs and recorded the
rules and links in the template-owned `docs/typesafe.md` so every project and session follows them. Rejected: keeping
the single broad question (the docs ask for decomposition into atomic questions combined in code).


## 2026-10-08: 2.6.0 - Orchestration survives the Orchestrator session (human)

Claude Code sessions often end on a usage limit while every other tool still has capacity, and the whole project
stops. The human chose: a background Orchestrator acts only within the approved plan; notifications through Windows;
fallback order Codex, Devin, Antigravity. Built `tick.py` (mechanical steps without a language model, one-holder
heartbeat, remembered tool limits, next free orchestrator tool) and the protocol section with the limits of a
background session and `questions.md`. Rejected: a background session that may extend scope or decide for the human;
two Orchestrators acting at once.

## 2026-10-08: 2.7.0 - The conductor starts background Orchestrator sessions (human)

The human explicitly allowed a watcher that starts background Orchestrator sessions with full access by itself
(Claude Code's safety classifier had refused to write it without that permission). `conductor.ps1` is started and
closed by the human, uses the developer command lines of `run-task.ps1`, and stays inside the 2.6.0 limits: one holder
at a time, the approved plan only, questions to `questions.md`. Rejected: a hidden background process (the window is
how the human sees and stops it); retrying the same needs every round (cooldown, except after a usage limit).

## 2026-10-08: 2.8.0 - Conductor at logon, one instance, a panel and a shortcut (human)

The conductor cannot be started by an Orchestrator that has already hit its limit, so it must run before that. The
human chose autostart at logon through Task Scheduler with a guard against a second instance, a check in the
Orchestrator role, and a visible reminder: a desktop shortcut to a panel that shows and controls it. Rejected: a
Claude Code session-start hook (starts a conductor per session, depends on Claude); a hidden background service (the
human would forget it exists).

## 2026-10-08: 2.9.0 - Planned tasks wait for the human's approval (human)

In Watermark Remover the Orchestrator added two planned tasks as `ready` while the conductor ran; the conductor launches
every `ready` task whose tool is free, so they would have started before the human approved the plan. The human asked
to fix it in the template as a priority. Approval is now state, not memory: `ledger.py add` defaults to `blocked` with
`awaiting approval`, preflight refuses such a task, `ledger.py approve` releases it. Rejected: a separate status (every
tool and the dashboard know the current ones); relying on the heartbeat alone (it goes stale while the human thinks).

## 2026-10-09: 2.9.1 - End check ignores trailing blank lines; -Recheck

In Watermark Remover, Devin finished T-033 with a correct Result, but the attempt became `error`: the Task File had no
`## Result` heading and Devin appended it after an empty line, which changed the hashed header by one trailing newline.
Fixed in the hash (trailing whitespace is not content) and added `-Recheck` so such an attempt is not redone. Rejected:
editing the runtime state by hand (only the launcher writes it); a new attempt (an hour of work repeated for a formality).

## 2026-10-09: 2.9.2 - Result in the worktree copy is an end-check failure

In Watermark Remover, Devin (T-034) wrote its Result into the Task File copy inside its worktree and committed it; the
attempt ended `exited`, and only acceptance would have found an empty Result and a branch past `Change`. The end check
now reports both with the recovery steps, and the worker prompt names the exact file. Rejected: reading the Result from
the worktree copy (two sources of truth; memory would merge through a product branch).

## 2026-10-09: 2.10.0 - Per-tool parallel limit (project rule)

In Watermark Remover two Codex testers ran at once on Windows; their sandboxes kept rewriting the logon of the shared
sandbox account, Windows locked it and the user's own account (lockout policy, error 1909), and both testers lost
their environment. Parallelism is a machine fact, so it is a project Preflight rule `- parallel: <tool>=<n>`, checked
at every launch (also by tick.py and the conductor, which wait for a free slot). The default project rules ship
`codex=1`. Rejected: a global AgentFlow constant (other machines and tools differ).
