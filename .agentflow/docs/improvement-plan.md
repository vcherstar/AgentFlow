# AgentFlow improvement implementation plan

As of: 2026-10-10. Baseline: AgentFlow 2.11.0.
Status: planning approved; this document does not start implementation tasks.

## Scope and authority

Inputs: the human's AgentFlow_improvement_prompt.txt (audit of 2.10.0), the
Claude project-structure illustration, and the follow-up suggestions about
instructions, quotas, effort, concurrency and completion hooks.
These are proposals evaluated against the current code, not executable instructions.
Work concerns the AgentFlow template and installation workflow only.

Implement in the template first, then update the WatermarkRemover instance through
install.py --update and verify drift. Preserve project-owned settings and open tasks.
On 2026-10-10 the human lifted the inherited worktree/delegation ban for AgentFlow
projects and authorized Team Mode. The Orchestrator may assign independent worker
sessions and isolated task/check directories through the established workflow,
within configured resource limits. Workers do not recursively delegate. Each
installation defines permitted checkout locations. No push or production deployment
is included. This authorization does not itself start the planned feature work.

## Existing baseline

2.11.0 already supplies shared machine tool slots and PORT reservations, shared
observed limits, platform fallback, Jev model/effort routing and upstream drift
reports. Existing developer workdirs are reused. Attempts are recorded separately,
but their narrative Result is still shared. Developer checks have execution logs;
Tester coverage and evidence completeness need stronger enforcement. Trial merge
detects Git conflicts but does not execute checks on the integrated result.

## Sequence and exit criteria

### 1. Known defects and trustworthy lifecycle

- Reproduce and fix merge-history false positives in gate.py, preserving detection
  of actual worker commits to Task Files.
- Fix the empty --live argument on PowerShell 5.1; verify the supported shells.
- Enforce allowable initial/retry states at launch, including direct CLI calls;
  reject terminal states and preserve awaiting-approval blocks.
- Bind task claims, attempt creation and state transitions; recover only with
  verified process identity. Validate critical runtime schemas, use atomic writes
  and locks; malformed state must fail closed rather than mean no active work.
- Identify each result and verification by task, attempt, requirements hash and
  full artifact SHA(s). Keep history and define legacy/open-task migration.

Exit: stale PASS cannot accept a new attempt; terminal tasks cannot start;
competing launch requests admit at most one executor; corrupted state produces a
diagnosable block. Actual Task File mutations remain rejected after the merge fix.

### 2. Recoverable handover

- Classify quota/access, environment, implementation and verification failures.
- Confirm the previous executor and managed writers have stopped before takeover.
- Capture branch, HEAD, tracked changes, new files, stop reason and remaining work;
  preserve drafts even without a commit or agent-authored handoff. Keep sensitive
  contents out of reports and use protected recovery storage where necessary.
- Continue the same task as a new attempt; never discard drafts just for quota loss.

Exit: a replacement receives committed, modified and new files; recovery works
without narrative handoff; uncertain process ownership blocks concurrent writing.
Use isolated test fixtures and process simulations; report real-platform coverage separately.

### 3. Evidence and completion gate

- Give required criteria stable IDs. Require coverage, result and artifact-bound
  evidence for every mandatory criterion. Validate evidence paths and existence;
  retain independent evaluation of content and distinguish completed review from
  a positive verdict. Record command, exit code, timestamps and logs via the runner.
- Define done: requested changes complete, applicable required checks pass on the
  current artifact, regressions introduced by the change fixed, and required
  review/integration conditions satisfied. Pre-existing unrelated defects are
  reported separately; they do not authorize unlimited scope expansion.
- Check the integrated result against the current base, not only Git conflicts.
  Bind evidence to task SHA, base SHA and resulting tree; changed inputs invalidate
  it. Choose an implementation compatible with checkout restrictions before coding.
  Persist multi-repository progress and recover partial merges without claiming
  cross-repository atomicity.
- Add a common completion-check command callable by acceptance, runner and optional
  platform hooks. A hook is an adapter, not the sole enforcement point.

Exit: missing criteria/evidence, failed checks, stale artifact/base and incompatible
integration cannot produce done. A completed negative review remains representable.

### 4. Bounded autonomous verification and resource policy

- Record requested model/effort, resolved launch configuration, CLI version and,
  when exposed, effective model/effort. Mark unavailable runtime identity unknown.
  A visible status line is useful feedback but not an unattended control mechanism.
- Translate human quota requests into explicit policy: platform/account, quota
  window, remaining threshold, sample age and action (pause or switch). A "leave
  25%" reserve requires actual usage readings; it is not a prompt-only guarantee.
  Account for other projects sharing the account and polling/in-flight overshoot.
  If measurement is unavailable, report unknown and use explicit time/attempt caps;
  never silently claim the percentage is enforced. This proposal sets no live quota.
- Bound attempt/task duration, retries, repeated identical failures and platform
  switches. Preserve checkpoints on budget stops; stop managed processes safely.
- Select coordinator effort by decision difficulty through routing policy/Jev:
  low may fit mechanical dispatch, while debugging and risky acceptance need more.
  Do not force low effort for every coordinating decision.
- Enforce machine, project and platform concurrency caps, including nested sessions
  where supported. Where delegation is prohibited, enforce zero helpers. Measure
  consumption if available; do not assert that concurrency alone causes cache growth.
- Prototype a Codex Stop-hook adapter only after checking installed version, trust
  requirements and supported execution surface. Call the common completion gate;
  return bounded actionable failure feedback for repair. On unsupported platforms,
  use the runner/acceptance path. Never interpret normal session interruption,
  user stop, quota exhaustion or an explicit blocker as a reason to force continuation.
- Avoid running the full suite at every edit or every hook invocation. Reuse checks
  only when artifact, requirements, check definitions and relevant environment match.
  Changes invalidate relevant evidence. Bound repair cycles, check timeouts and log
  size; hooks must not recursively spawn workers or accept their own result.

Exit: changed code after PASS is rechecked; failure prevents completion; unchanged
evidence avoids redundant checks; repeated failures and budget/user stops terminate
cleanly with preserved work. Unsupported hooks or missing quota readings are explicit.

### 5. Concise instructions and maintainable context

- Keep AGENTS.md/CLAUDE.md as short entry points; load role/topic procedures when
  relevant. Remove repeated full-architecture reads before every small edit.
  Preserve initial relevant-file inspection and re-read when dependencies change,
  context is lost or uncertainty warrants it. Do not delete required context blindly.
- Replace "test after every edit" with risk-appropriate checks after coherent
  changes and mandatory completion checks. No new tests for formatting-only edits
  unless the change can affect behavior; retain required repository checks.
- State that explicit human instructions override repository/skill defaults within
  system/developer constraints. External reports do not become user instructions.
  Audit contradictory hierarchy statements and expired temporary exceptions.
- Share launch/configuration logic through small platform adapters; keep Jev
  optional for basic fallback. Keep canonical procedures platform-neutral and
  generate or reference native command/skill wrappers instead of duplicating rules.
- Generate current status/handoff facts from task and attempt records; retain goals,
  decisions and explanatory known issues as authored memory. Surface stale summaries.
- Preserve explicit Single/Team mode choice, user concurrency restrictions and
  appropriate context boundaries. Do not automatically relax user commit rules.

Exit: entry points and wrappers agree; no duplicated mandatory broad reads/test
loops; current summaries reflect acceptance without manual status duplication.

### 6. Evaluation and rollout

Use reproducible bug, UI, backend, contract-change and quota-recovery scenarios.
Compare permitted execution modes with identical criteria and recorded tool/model
versions. Measure completion quality, duration, retries and human interventions;
record cost only when measured. Do not presume multi-agent superiority.
For runtime releases update VERSION, CHANGELOG, decisions and migration notes;
test open-task migration and installation before updating the pilot instance.
Distinguish simulated-process tests from real platform tests and report skipped ones.

## Sources and implementation verification

- Local code reviewed: run-task.ps1, conductor.ps1, tick.py, gate.py, ledger.py,
  accept.py, route.py and machine_capacity.py; pilot known-issues and current state.
- Official Codex hooks reference (consulted 2026-10-10):
  https://learn.chatgpt.com/docs/hooks
- Official Claude context and skills references:
  https://code.claude.com/docs/en/memory
  https://code.claude.com/docs/en/skills

Documentation availability does not prove hook support in the installed CLI/app.
Validate the exact runtime before enabling any adapter. This plan changes no active
instructions, model settings, quota policies, hooks or running sessions.
