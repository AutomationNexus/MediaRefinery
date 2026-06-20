# Security Policy

## Supported Versions

Security fixes currently target the default branch and the `2.1.x` release line.

Review [docs/getting-started/installation.md](docs/getting-started/installation.md), [docs/admin/operations.md](docs/admin/operations.md), and [docs/security/threat-model.md](docs/security/threat-model.md) before deploying MediaRefinery for real users.

## Reporting a Vulnerability

Please do not file a public issue for undisclosed security vulnerabilities.

Use GitHub's **Report a vulnerability** flow for this repository. Include:

- A short description.
- Affected component, if known.
- Reproduction steps.
- Impact assessment.
- Preferred disclosure timeline.

Maintainers should acknowledge reports within a few business days and coordinate a fix, advisory, and release notes before public disclosure.

## Scope

In scope:

- The MediaRefinery application code.
- The FastAPI service and React dashboard.
- Docker and Compose defaults in this repository.
- Documentation or defaults that could cause unsafe handling of secrets or private media.

Usually out of scope:

- Immich itself.
- Operator host security.
- Reverse proxy configuration outside the examples.
- Stolen credentials unrelated to MediaRefinery behavior.

## Secure Defaults

- Do not commit secrets, `.env` files, logs, SQLite state, model files, thumbnails, video frames, or user media.
- Back up `/data/master.key` with `state.db`; Immich API keys are encrypted in the database.
- Run the service behind HTTPS for real users (`system.base_url` in `config.db`).
- Treat `/data/state.db`, `/data/databases/config.db`, and `/data/master.key` as one sensitive backup unit.
- Review [docs/security/threat-model.md](docs/security/threat-model.md) before changing auth, logging, model downloads, scan execution, or Immich mutation code.
