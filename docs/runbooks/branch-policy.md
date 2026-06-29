# Branch Policy — MediaRefinery

GitHub branch protection is unavailable on the current plan. CI guards and local git hooks enforce the same rules.

## Protected branches

| Branch | How changes land |
|--------|------------------|
| `dev` | Feature branch → PR → CI green → merge; GitHub auto-deletes the feature branch |
| `main` | `Promote dev to main` workflow after dev CI is green |

Direct `git push` to `dev` or `main` is blocked locally (`.githooks/pre-push`) and fails CI if bypassed.

## Feature branch workflow

```cmd
git checkout dev
git pull origin dev
git checkout -b mr-fix-short-description
REM ... edit, commit ...
python -m ruff check src tests tools
python -m pytest -q
cd frontend && npm run typecheck && npm test -- --run && npm run build
git diff --check
git push -u origin HEAD
gh pr create --base dev --title "Short title" --body "Summary and test plan"
```

Feature branch names should use the `mr-` prefix (e.g. `mr/fix-scan-pipeline`).

After CI is green on the PR, merge on GitHub. GitHub's `delete_branch_on_merge` setting automatically deletes merged same-repo feature branches; if GitHub cannot delete a branch, delete it manually.

## Local hook setup (once per clone)

```cmd
tools\install-githooks.cmd
```

Or manually: `git config core.hooksPath .githooks`

Requires Git Bash (Git for Windows). The hook blocks `git push` to `dev` and `main`.

## Promotion to main

Never push `dev` to `main` manually. Normal path:

1. Ensure latest `dev` CI is green.
2. Run the **Promote dev to main** workflow in GitHub Actions.
3. Wait for `main` CI to pass.
4. Tag stable baselines when appropriate (see `docs/releases/release-checklist.md`).

## Agent rules

- Never `git push origin dev` or `git push origin main`.
- Never ask the user to bypass CI guards.
- Never commit `master.key`, `data/**`, or `config.db`.
- Create/use a feature branch with `mr-` prefix, open PR, wait for CI, merge via `gh pr merge` only after user approval.
