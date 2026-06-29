# MediaRefinery OpenCode Rules

This repo is MediaRefinery — a self-hosted Immich review companion with a FastAPI backend, React dashboard, and SQLite state store.

## Shell (Windows local dev)

- Chain commands with `;`, not `&&` or `||`.
- Use `gh --repo automationnexus/MediaRefinery` outside the clone.
- Trim long output with `Select-Object -Last N`; use `gh run view --log-failed` for CI failures.
- CI workflows (`.github/workflows/`) stay bash on `ubuntu-latest`.

## Safety Rules

- Start every task with `git status --short --branch` before edits.
- Create a feature branch from `dev` with `mr-` prefix; never commit on `dev` or `main`.
- Never read, print, summarize, copy, edit, or commit credentials (`.env*`, `config.db`, `master.key`, Immich API keys, session tokens).
- Never create or track `AGENTS.md` or `CLAUDE.md`.
- Do not commit `opencode.json` or `.opencode/`.

## QA Gates

Before opening a PR:

- `git status --short --branch`
- `ruff check src tests tools`
- `python -m pytest tests/ -q`
- In `frontend/`: `npm run typecheck`, `npm test -- --run`, `npm run build`
- `git diff --check`

Never push directly to `dev` or `main`. Feature branch → PR to `dev` → CI green → merge. Promote to `main` only via the GitHub Actions workflow.

## Agent Workflow

- New sessions start in `plan` mode (read-only). Switch to `build` with Tab or `/mr-execute` after plan approval.
- `build` delegates to `@mr-backend-engineer`, `@mr-frontend-engineer`, `@mr-qa-gatekeeper`, `@mr-reviewer`.
- Use `@mr-opus-solver` only for hard cross-module bugs.
- Land git changes with a feature branch and PR to `dev`.

## Local OpenCode Setup

- `opencode.json` and `.opencode/` are local-only.
- Committed seeds live in `tooling/opencode/`; bootstrap mirrors them into `.opencode/`.

## Token-Efficient Handoff

Before switching agents: goal (1 line), files touched (paths), branch/status, decisions (≤5), remaining (≤5), validation result, risks. No diffs, logs, or secrets.

## MediaRefinery Conventions

- Python: `src/mediarefinery/`. Frontend: `frontend/` → built statics into `src/mediarefinery/web/`. Tests: `tests/`.
- Example config: `templates/config.example.yml`.
