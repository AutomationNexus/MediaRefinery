---
description: Run local QA checks and report pass/fail blockers.
argument-hint: [optional scope]
---

Dispatch `qa-gatekeeper`: `git status --short --branch`, `ruff check src tests tools`,
`python -m pytest tests/ -q`, and in `frontend/`: `npm run typecheck`, `npm test`,
`npm run build`, then `git diff --check`. Return pass/fail and actionable blockers only.
No file edits. Arguments: $ARGUMENTS
