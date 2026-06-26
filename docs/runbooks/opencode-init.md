# Opencode Init Prompt

Use this prompt when handing the MediaRefinery repo to opencode or another coding agent.

## Local OpenCode setup

- `opencode.json` and `.opencode/` are local-only. Never commit them.
- Bootstrap on a new machine: run `tools\bootstrap-opencode.ps1` (or `tools\bootstrap-opencode.cmd`). This copies `opencode.json.example` to `opencode.json` when missing, and mirrors committed seeds from `tooling/opencode/` into `.opencode/`.
- Committed templates: `opencode.json.example` and `tooling/opencode/` (project rules, plan/build instructions, agents, commands).
- New sessions default to **plan** mode (`cursor-acp/composer-2.5`, read-only). Switch to **build** with Tab or run `/mr-execute` after plan approval. Build uses the same model with `.opencode/project-rules.md` and `.opencode/build-instructions.md`.
- Built-in `general`, `explore`, and `scout` agents are disabled in `opencode.json`.

```text
You are working in C:\Users\Tahasanul\Desktop\RemoteRepo\GitHub\MediaRefinery.

This is MediaRefinery — a self-hosted Immich review companion with a FastAPI backend, React dashboard, and SQLite state store. Never commit secrets, master.key, config.db, /data/ runtime state, or machine-local AI files.

Start by reading:
- README.md
- docs/runbooks/branch-policy.md
- docs/development/local-development.md
- docs/development/repository-structure.md
- docs/guides/dashboard.md
- docs/admin/configuration.md

Branch model:
- dev is the workbench branch for features, tests, and proposed changes.
- main is the stable production branch.
- Never push directly to dev or main. Use feature branches (prefix mr-), open PRs to dev, merge after CI is green, then delete the feature branch.
- Stable release is dev to main through the "Promote dev to main" GitHub Actions workflow after dev CI is green.
- The repo is private, and GitHub branch protection is unavailable on the current plan, so CI and .githooks/pre-push enforce policy for dev and main.
- Read docs/runbooks/branch-policy.md for the exact agent workflow.

Local access:
- Real credentials and runtime state live only in ignored local files such as master.key, config.db, and /data/.
- Do not print, commit, summarize, or copy credential values.
- Agents must not read or edit master.key, data/**, or config.db.

Normal workflow:
1. Run git status --short --branch and confirm the working tree before changing files.
2. Work on a feature branch from dev (never commit directly on dev or main).
3. Never track AGENT-HANDOFF.md, opencode.json, or .opencode/.
4. For OpenCode on a new machine, run tools\bootstrap-opencode.ps1 (creates opencode.json if missing and syncs tooling/opencode to .opencode/). New sessions start in plan mode; say go to switch to build or run /mr-execute after plan approval.
5. Before committing, run:
   ruff check src tests tools
   python -m pytest tests/ -q
   cd frontend && npm run typecheck && npm test -- --run && npm run build
   git diff --check
6. Push the feature branch and open a PR to dev; merge after CI is green; delete the feature branch.
7. Promote dev to main only with the GitHub Actions workflow after dev CI succeeds.

OpenCode commands:
- /mr-qa — local QA gate
- /mr-prepush — PR readiness check
- /mr-execute — approved plan execution (build agent)
- /mr-release — dev-to-main release workflow
- /mr-frontend — React dashboard specialist work

Hidden agents: mr-architect, mr-backend-engineer, mr-frontend-engineer, mr-qa-gatekeeper, mr-reviewer, mr-opus-solver
```
