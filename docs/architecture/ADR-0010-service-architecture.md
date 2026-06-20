# ADR-0010: Service Architecture

## Status

Accepted.

## Context

MediaRefinery's public product direction is the web service: a FastAPI backend, a React dashboard, a SQLite state store, and local model execution beside an Immich server.

The repository still contains mature core pipeline modules for configuration, scanning, extraction, classification, decisions, actions, reporting, and Immich integration. Those modules are kept as shared implementation code because the service runner imports them directly. They are not the public release surface.

## Decision

MediaRefinery ships the service as the public product line.

1. End users run the Docker service. The service exposes the dashboard and the `/api` HTTP API from one origin.
2. The installable console script surface is limited to `mediarefinery-service`.
3. The core pipeline modules remain in the package as internal/shared code used by the service runner and developer validation.
4. The service uses Immich proxy login. It stores encrypted Immich session tokens and optional encrypted user API keys. It does not store user passwords.
5. The service stores multi-tenant state in `state.db`. There is no in-place migration from older single-user state databases.
6. No model weights are bundled. The dashboard installs catalog models only after HTTPS download, SHA256 verification, and license acceptance.
7. Hide semantics use Immich's Locked Folder. Forward moves use an API key. Revert uses a PIN-unlocked Immich session proxied by the backend; the PIN is not logged or persisted.
8. Audit logging is required for user-visible state changes, Immich mutations, model installs, license acceptance, scan starts/finishes, and undo/revert operations.

## Consequences

- Documentation and examples should describe the service first.
- Docker and environment examples should not advertise the retired CLI workflow as an end-user path.
- The `/api` prefix is an API compatibility prefix, not a product-version claim.
- Shared core modules can be refactored over time, but only after the service runner has replacement code and tests.
- Releases are gated by the release checklist, final CI, package inspection, Docker validation, and compatibility smoke notes.
