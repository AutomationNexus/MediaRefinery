---
description: Run local QA checks and report pass/fail blockers.
argument-hint: [optional scope]
---

Dispatch the `qa-gatekeeper` subagent to run this repository's local QA gate (the
commands in CLAUDE.md's "QA gates" section). Return pass/fail and actionable blockers
only. No file edits. Scope/arguments: $ARGUMENTS

<!-- repo-specific -->

QA gate: `ruff check src tests tools`, `python -m pytest tests/ -q`, and in
`frontend/`: `npm run typecheck`, `npm test -- --run`, `npm run build`, then
`git diff --check`.
