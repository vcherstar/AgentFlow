# Agent instructions

This repository is the AgentFlow template. Its product is `.agentflow/` plus the entry points and the guide.

<!-- agentflow:begin -->
## AgentFlow

AgentFlow version: 2.5.2. Workflow files live in `.agentflow/`; this block is template-owned and replaced on update.

Source of truth: `.agentflow/docs/ai-handoff-protocol.md`. Read it first, then follow it. Project rules:
`.agentflow/docs/project-rules.md` (and any rules in this file outside this block).

- With a role (`.agentflow/roles/<role>.md`, or `/start-role <role> ...`): protocol section "Starting a role session".
  Workers (developer, tester, deployer) do not update project memory.
- Without a role (Single Mode): protocol section "Starting a new AI session" before substantial work; "Updating memory"
  before ending a long session.
<!-- agentflow:end -->

## Project rules

- This repository develops the template itself: template-owned files are its product (`.agentflow/README.md`).
- A change to the protocol, a role, a tool or the dashboard gets a `CHANGELOG.md` entry, a version bump in
  `.agentflow/VERSION` and a dated entry in `.agentflow/state/decisions.md`; its migration notes say what an
  installed project must do.
- Keep `.agentflow/tools/install.py` and these entry points in step: `.agentflow/tests/test_install.py` checks it.
- Before a commit: `python -m unittest discover -s .agentflow/tests`.
