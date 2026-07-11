# CLAUDE.md — MediaRefinery

Self-hosted Immich review companion: FastAPI backend, React dashboard, SQLite state store.

## Conventions

- Python: `src/mediarefinery/`. Frontend: `frontend/` → built statics into
  `src/mediarefinery/web/`. Tests: `tests/`.
- Example config: `templates/config.example.yml`.

## Branch policy

- Feature branch prefix: `mr-`, created from `dev`. Never commit on `dev` or `main` directly.
- Start every task with `git status --short --branch` before edits.
- Never push directly to `dev` or `main`. Feature branch → PR to `dev` → CI green → merge.
  Promote to `main` only via the GitHub Actions **Promote dev to main** workflow.
- **Auto-versioning**: "Promote dev to main" takes a `bump-type` dispatch input
  (`patch` default / `minor` / `major`) that computes the next `X.Y.Z` from the
  latest `vX.Y.Z` tag on `main` and writes it to `pyproject.toml` on the promote
  branch. Never hand-edit the version yourself.
- Org-wide CI/PR flow, branch rules, and auto-versioning: see `../CLAUDE.md`
  (the AutomationNexus GitHub workspace root) — consult it first for anything
  not covered here, or if CI/promote looks broken.

## Shell (Windows local dev)

- Chain commands with `;`, not `&&`/`||`.
- Use `gh --repo automationnexus/MediaRefinery` outside the clone.
- Trim long output with `Select-Object -Last N`; use `gh run view --log-failed` for CI failures.
- CI workflows (`.github/workflows/`) stay bash on `ubuntu-latest`.

## QA gates (run before every PR)

```
git status --short --branch
ruff check src tests tools
python -m pytest tests/ -q
```
In `frontend/`: `npm run typecheck`, `npm test -- --run`, `npm run build`, then
`git diff --check`.

## Execute pipeline (risk-based)

Pick the track based on the size/risk of the approved plan; both still land through a
feature branch → PR → CI → merge, never a direct push to `dev`/`main`.

**Small/low-risk** (single file, docs-only, no API/scan-pipeline behavior change): the main
session may implement directly, then run the QA gate itself and a self-review pass before
opening the PR. No need to dispatch every subagent for a one-file fix.

**Multi-file or behavior-risk** (touches `src/mediarefinery/` or `frontend/`): full
pipeline — `backend-engineer`/`frontend-engineer` → `qa-gatekeeper` → `reviewer` → PR.

## Subagents

| Agent | Use for | Model |
|-------|---------|-------|
| `architect` | API design, Immich integration, scan pipeline, release risk — before implementing | sonnet, high effort |
| `backend-engineer` | `src/mediarefinery/` FastAPI/Immich/ONNX/OCR/scan pipeline | sonnet |
| `frontend-engineer` | `frontend/` React dashboard SPA | sonnet |
| `qa-gatekeeper` | Local QA gate — pass/fail report only, no edits | haiku |
| `reviewer` | Independent security/encryption review before PR | sonnet |
| `security-auditor` | Secrets, workflow safety, dependency risk — before release | sonnet, high effort |

For hard cross-layer conflicts, switch the main session to opus (`/model opus` or
`opusplan`) rather than a dedicated solver agent.

The 4 core roles (`architect`, `qa-gatekeeper`, `reviewer`, `security-auditor`) are the
org-standard shared core defined in `automationnexus/.github`'s
`templates/_shared/.claude/`, with `backend-engineer`/`frontend-engineer` layered on top
as this repo's domain-specific engineers. For how this team fits into the org-wide agent
model across repos, see the workspace-root `CLAUDE.md`'s "Agent organization" section.

## Slash commands

`/execute` (full build pipeline), `/frontend` (frontend-engineer only), `/qa` (QA gate),
`/prepush` (PR readiness check), `/release` (dev→main promotion workflow).

## Shared CI — do not inline

- **Never inline or fork `automationnexus/.github` reusable-workflow logic** into this
  repo's own workflow files, even temporarily. Always call it via
  `uses: automationnexus/.github/.github/workflows/<name>.yml@v1`.
- If this repo needs CI behavior the shared workflow doesn't support, the fix is a new
  **generic** input on the shared workflow (contributed to `automationnexus/.github`), never
  a local copy/paste workaround. Precedent: `build-args`, `main-source-allow-glob`, `exclude-paths`.
- Never use `GITHUB_TOKEN` or a personal access token for cross-branch or cascade automation
  (nightly, promote, release) — only the CI-Bot GitHub App.
- Before touching any `.github/workflows/*.yml` file here, check `automationnexus/.github`
  first — most CI behavior lives there, not in this repo's thin wrapper.

## Secrets / never read or print

- Never read, print, summarize, copy, edit, or commit credentials (`.env*`, `config.db`,
  `master.key`, Immich API keys, session tokens). `.claude/settings.json` already denies
  these paths — do not weaken it.

## Do not

- Do not add model/provider/router config anywhere in this repo. Claude Code talks directly
  to Anthropic with the operator's own account.
