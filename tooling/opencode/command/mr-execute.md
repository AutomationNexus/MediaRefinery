---
description: Execute approved plan through expert agents (implement, QA, review, optional PR).
agent: build
---

Run the MediaRefinery execute pipeline for an approved plan.

Steps:
- Run `git status --short --branch` and confirm an `mr-` feature branch (not `dev` or `main`). Create one from updated `dev` if needed.
- Invoke `@mr-backend-engineer` to implement or verify Python/FastAPI/Immich changes from the approved plan.
- Invoke `@mr-frontend-engineer` when the plan touches `frontend/`.
- Invoke `@mr-qa-gatekeeper` to run the full `/mr-qa` local gate.
- Invoke `@mr-reviewer` for independent review of changed files.
- Stop on the first failed gate.
- Push the feature branch and open a PR to `dev` (never push directly to `dev` or `main`).

Return a compact handoff: agents used, commands run, pass/fail. Arguments: `$ARGUMENTS`.
