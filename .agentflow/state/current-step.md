# Current Step

## Now

AgentFlow 2.11.0 is on `main` (merged): shared machine capacity across projects, effort routing in
route.py, upstream template sync. Watermark Remover was updated to 2.11.0 and reports no drift.

## Next action

1. Watch `upstream.py` drift reports from installed projects; port upstream candidates into the
   template generalized (CHANGELOG, VERSION bump, decisions.md entry).
2. `git push` only on the human's word (`main` is ahead of `origin/main`).
