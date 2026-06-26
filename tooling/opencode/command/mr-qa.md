---
description: Run local QA checks and report pass/fail blockers.
agent: mr-qa-gatekeeper
---

Run local QA for this repo.

Steps:
- Run `git status --short --branch`.
- Run `ruff check src tests tools`.
- Run `python -m pytest tests/ -q`.
- In `frontend/`: run `npm run typecheck`, `npm test`, and `npm run build`.
- Run `git diff --check`.

Return pass/fail and actionable blockers only. Do not edit files. Arguments: `$ARGUMENTS`.
