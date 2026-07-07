---
description: Execute an approved plan through the expert subagents (implement, QA, review, optional PR).
argument-hint: [optional focus notes]
---

Run the MediaRefinery execute pipeline for an approved plan: $ARGUMENTS

0. If this is a single-file/low-risk change, stop — implement it directly in the main
   session per CLAUDE.md's risk tiering instead of running this full pipeline.
1. `git status --short --branch` — confirm an `mr-` feature branch (not `dev`/`main`);
   create one from updated `dev` if needed.
2. Dispatch `backend-engineer` to implement/verify Python/FastAPI/Immich changes.
3. Dispatch `frontend-engineer` when the plan touches `frontend/`.
4. Dispatch `qa-gatekeeper` for the full `/qa` local gate.
5. Dispatch `reviewer` for independent review of changed files.
6. Stop on the first failed gate.
7. Push the feature branch and open a PR to `dev` (never push directly to `dev` or `main`).

For hard cross-layer conflicts, escalate by switching the main session to opus
(`/model opus` or `opusplan`) rather than a dedicated solver agent.
