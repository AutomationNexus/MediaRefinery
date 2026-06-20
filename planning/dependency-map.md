# Dependency Map

This map reflects the current service product line. The older CLI-era modules are treated as shared core pipeline code because the service runner still imports them.

## Runtime Graph

```mermaid
flowchart TB
  subgraph service [Service product]
    APP[FastAPI app]
    API[/api routes]
    AUTH[Immich proxy auth]
    SEC[security helpers]
    SCHED[scheduler and auto-scan]
    RUNNER[service runner]
    STORE[StateStore]
    WEB[React dashboard bundle]
    MODELS[model catalog and lifecycle]
  end

  subgraph core [Shared core pipeline]
    CFG[config validation]
    IMM[Immich client adapters]
    SCAN[AssetScanner]
    EXT[MediaExtractor]
    CLF[classifier backends]
    DEC[DecisionEngine]
    ACT[ActionExecutor]
    REP[Reporter]
  end

  WEB --> API
  APP --> API
  API --> AUTH
  API --> SEC
  API --> STORE
  API --> SCHED
  API --> MODELS
  SCHED --> RUNNER
  RUNNER --> CFG
  RUNNER --> IMM
  RUNNER --> SCAN
  RUNNER --> EXT
  RUNNER --> CLF
  RUNNER --> DEC
  RUNNER --> ACT
  RUNNER --> STORE
  REP --> STORE
```

## Module Responsibilities

| Module / area | Role |
|---------------|------|
| `service.app` | Creates the FastAPI app, mounts routers, configures lifespan jobs, and serves the dashboard. |
| `service.routers` | Owns the HTTP API for auth, setup, scans, models, audit, assets, settings, and health. |
| `service.auth` | Proxies Immich login, revalidates sessions, and manages encrypted session tokens. |
| `service.security` | Master-key loading, authenticated encryption, cookies, CSRF, rate limiting, and JSON logging. |
| `service.state_store` | Multi-tenant SQLite schema and user-scoped accessor. |
| `service.runner` | Adapts the shared core pipeline to user-scoped service scans. |
| `service.scheduler` | Enforces per-user scan concurrency and quota. |
| `service.auto_scan` | Polls Immich for new assets and submits scans when a user is due. |
| `service.model_catalog` / `service.model_lifecycle` | Lists, downloads, verifies, installs, and removes ONNX models. |
| `service.classifier_cache` | Caches ONNX classifier sessions for active catalog models. |
| `service.locked_folder` | Proxies PIN unlock and reverts Locked Folder moves without persisting the PIN. |
| `frontend/` | Dashboard source. `npm run build` emits static assets into `src/mediarefinery/web/`. |
| `scanner`, `extractor`, `classifier`, `decision`, `actions`, `immich`, `config` | Shared core pipeline used by service scans and developer validation. |

## Release-Critical Dependencies

- Production startup builds runner factories through `service.production`: stored API keys feed `HttpImmichClient`, and active model hashes feed `ClassifierSessionCache`.
- Manual scans and auto-scan share the same production runner factories.
- The first-run dashboard installs a model, validates a user API key, stores it encrypted, and then starts the first scan.
- Docker builds must include the frontend bundle and ONNX dependencies.
- The release workflow must publish only after Python, frontend, Docker, live Immich smoke, and vulnerability-audit gates pass.

## Public Surface Rule

The public surface is the service, dashboard, Docker image, and `/api` API. The shared core pipeline is implementation code. Do not add new user-facing docs that route people through the retired CLI workflow unless the feature is explicitly reintroduced as supported tooling.
