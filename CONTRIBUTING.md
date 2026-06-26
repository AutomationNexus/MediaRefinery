# Contributing to MediaRefinery

Thanks for helping improve MediaRefinery. This project handles private media, so correctness, privacy, and clear review notes matter more than speed.

## Start Here

1. Read [SECURITY.md](SECURITY.md).
2. Read the local development guide in [docs/development/local-development.md](docs/development/local-development.md).
3. For service changes, read [docs/security/threat-model.md](docs/security/threat-model.md).
4. For Immich API changes, read [docs/reference/immich-api-compat.md](docs/reference/immich-api-compat.md).
5. For release work, read [docs/releases/release-checklist.md](docs/releases/release-checklist.md).

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,service,onnx,ocr]"
cd frontend
npm install
```

## Quality Gates

Run these before opening a pull request:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests tools
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src tests

cd frontend
npm run typecheck
npm test
npm run build

git diff --check
```

Enable local push protection once per clone: `tools\install-githooks.cmd` (see [docs/runbooks/branch-policy.md](docs/runbooks/branch-policy.md)).

The longer contributor workflow, Docker smoke, and package validation steps are in [docs/development/local-development.md](docs/development/local-development.md).

## Security Rules

- Do not commit secrets, `.env` files, API keys, logs, SQLite databases, model weights, thumbnails, frames, or user media.
- Do not paste real user media, private paths, tokens, or PINs into issues or pull requests.
- Any change touching auth, cookies, CSRF, logging, model downloads, preview bytes, account purge, or Immich mutations must describe its privacy impact in the pull request.
- Report undisclosed vulnerabilities through GitHub private vulnerability reporting, not public issues.

## Pull Requests

- Keep one logical change per PR when possible.
- Use [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md).
- Explain the user-visible behavior change and the verification you ran.
- Link to updated docs when behavior changes.
- Prefer small, explicit tests around privacy and tenant isolation.

## Labels

Useful issue and PR labels:

| Prefix | Examples |
|--------|----------|
| `type:` | `type:feature`, `type:bug`, `type:docs`, `type:security`, `type:chore` |
| `priority:` | `priority:p0`, `priority:p1`, `priority:p2`, `priority:p3` |
| `area:` | `area:immich`, `area:scanner`, `area:classifier`, `area:state`, `area:frontend`, `area:docker`, `area:docs` |

## Branches and Commits

- Read [docs/runbooks/branch-policy.md](docs/runbooks/branch-policy.md). Never push directly to `dev` or `main`.
- Feature branch names: `mr/...` prefix (e.g. `mr/fix-scan-queue`).
- Conventional Commits are welcome: `type(scope): description`.
- OpenCode bootstrap (local only): `tools\bootstrap-opencode.ps1` — see [docs/runbooks/opencode-init.md](docs/runbooks/opencode-init.md).

## Code of Conduct

We follow the [Code of Conduct](CODE_OF_CONDUCT.md). Be respectful, specific, and constructive.
