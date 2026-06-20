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
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m compileall -q src tests

cd frontend
npm run typecheck
npm test
npm run build
```

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

- Branch names: `feat/...`, `fix/...`, `docs/...`, or `chore/...`.
- Conventional Commits are welcome: `type(scope): description`.

## Code of Conduct

We follow the [Code of Conduct](CODE_OF_CONDUCT.md). Be respectful, specific, and constructive.
