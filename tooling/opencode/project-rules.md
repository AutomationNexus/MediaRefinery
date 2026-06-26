# MediaRefinery OpenCode Rules

This repo is MediaRefinery — a self-hosted Immich review companion with a FastAPI backend, React dashboard, and SQLite state store.

## Safety Rules

- Start every task with `git status --short --branch` before edits.
- Create a feature branch from `dev` for all changes; never commit on `dev` or `main` directly.
- Use the `mr-` prefix for feature branch names (e.g. `mr/fix-scan-queue`).
- Never read, print, summarize, copy, edit, or commit credential values.
- Treat `.env`, `.env.*`, `config.db`, `master.key`, Immich API keys, and session tokens as private.
- Never create or track `AGENTS.md` or `CLAUDE.md`.
- Do not commit `opencode.json` or `.opencode/`.

## QA Gates

Before opening a PR, run local QA in the same task:

- `git status --short --branch`
- `ruff check src tests tools`
- `python -m pytest tests/ -q`
- In `frontend/`: `npm run typecheck`, `npm test`, `npm run build`
- `git diff --check`

Never push directly to `dev` or `main`. Use a feature branch, open a PR to `dev`, wait for CI green, merge, then delete the feature branch.

Promote `dev` to `main` only through the **Promote dev to main** workflow after dev CI is green.

Enable local hook once per clone: `tools\install-githooks.cmd` (blocks direct pushes to `dev`/`main`).

## Agent Workflow

- New sessions start in built-in `plan` mode (read-only). Switch to `build` with Tab or run `/mr-execute` after plan approval.
- For architecture, scan pipeline, Immich integration, or model lifecycle, invoke `@mr-architect` before finalizing the plan.
- When the user approves a plan and says go, build, or execute, run `/mr-execute` (built-in `build` orchestrator).
- `build` delegates to `@mr-backend-engineer` for Python/FastAPI, `@mr-frontend-engineer` for `frontend/`, `@mr-qa-gatekeeper` for local QA, and `@mr-reviewer` for independent review.
- Land git changes with a feature branch and PR to `dev`; never push directly to `dev` or `main`.
- Use `@mr-opus-solver` only for hard cross-module bugs, architecture conflicts, or cases where cheaper agents disagree.

## Local OpenCode Setup

- `opencode.json` and `.opencode/` are local-only and must not be committed.
- Copy from `opencode.json.example` when setting up a new machine, then run `tools\bootstrap-opencode.cmd`.
- Committed seeds live in `tooling/opencode/`; bootstrap mirrors them into local `.opencode/`.

## Token-Efficient Handoff

Before switching agents or models, write a compact handoff:

- Goal: one sentence.
- Files read/touched: paths only.
- Current branch/status: short.
- Decisions made: max 5 bullets.
- Remaining work: max 5 bullets.
- Validation run: commands and pass/fail only.
- Risks/blockers: actionable items only.

Do not paste large file contents, raw diffs, secrets, or full logs. Prefer paths, API route names, model IDs, command names, and short status lines.

## MediaRefinery Conventions

- Python package lives under `src/mediarefinery/`.
- Frontend dashboard under `frontend/`; built statics emit into `src/mediarefinery/web/`.
- Tests live under `tests/`; Playwright e2e under `tests/e2e_frontend/`.
- Example config: `templates/config.example.yml`.
- Keep changes minimal and run the full QA gate before PR.
