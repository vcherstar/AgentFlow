# /start-role

Run `.agentflow/docs/ai-handoff-protocol.md`, section "Starting a role session".

Arguments: `<role> [task file]` — role is one of `orchestrator`, `developer`, `tester`, `deployer` (files in `.agentflow/roles/`). Workers need a task file, e.g. `/start-role developer .agentflow/tasks/T-101-api.md`.

Given: $ARGUMENTS
