# MediaRefinery Documentation

Current release: **v1.0.0** — [GitHub Releases](https://github.com/automationnexus/MediaRefinery/releases)

The docs are organized like a typical service project:

- `getting-started/` for installation and first deployment;
- `guides/` for task-based product workflows;
- `admin/` for configuration, operations, and troubleshooting;
- `reference/` for API and compatibility details;
- `security/` for threat-model notes;
- `development/` for contributor setup and repository structure;
- `releases/` for release validation records;
- `architecture/` for architecture decisions.

## Getting Started

| Goal | Document |
|------|----------|
| Install with Docker, Compose, or source | [getting-started/installation.md](getting-started/installation.md) |
| Use the dashboard after first login | [guides/dashboard.md](guides/dashboard.md) |
| Install and manage models | [guides/models.md](guides/models.md) |

## Admin And Operations

| Goal | Document |
|------|----------|
| Configure system settings (`config.db`) and per-user dashboard settings | [admin/configuration.md](admin/configuration.md) |
| Run backups, health checks, logging, and upgrades | [admin/operations.md](admin/operations.md) |
| Diagnose setup, login, scan, model, OCR, and sampling issues | [admin/troubleshooting.md](admin/troubleshooting.md) |

## Reference

| Topic | Document |
|-------|----------|
| HTTP API | [reference/api.md](reference/api.md) |
| Immich endpoint compatibility | [reference/immich-api-compat.md](reference/immich-api-compat.md) |
| Model catalog metadata | [models/README.md](models/README.md) |
| Security boundaries and accepted risks | [security/threat-model.md](security/threat-model.md) |

## Development And Releases

| Topic | Document |
|-------|----------|
| Local development | [development/local-development.md](development/local-development.md) |
| Repository structure | [development/repository-structure.md](development/repository-structure.md) |
| Service architecture decision | [architecture/ADR-0010-service-architecture.md](architecture/ADR-0010-service-architecture.md) |
| Release checklist | [releases/release-checklist.md](releases/release-checklist.md) |
| Release readiness audit | [releases/release-readiness-audit.md](releases/release-readiness-audit.md) |

## What MediaRefinery Does

MediaRefinery connects to Immich, scans assets visible to signed-in users, stores derived analysis metadata, and gives users review queues in a local dashboard. It is meant to support human review and organization work.

It can:

- classify images through local ONNX models;
- optionally sample bounded frames from videos and animated GIFs;
- optionally extract OCR text locally;
- create review album and tag actions in Immich;
- move selected assets to Immich Locked Folder through an API key;
- group scanned assets into local event groups;
- search scanned assets by metadata, OCR text, and Immich Smart Search when available.

It does not:

- automatically delete or trash media;
- ship model weights in the repository or image;
- upload media bytes to cloud inference services by default;
- bypass Immich permissions;
- create its own face-recognition identity store.

## Deployment Shape

```text
Browser
  |
  | HTTPS recommended
  v
MediaRefinery service
  |-- FastAPI backend
  |-- React dashboard
  |-- SQLite state under /data
  |-- local ONNX/OCR runtimes
  |
  | HTTPS to system.immich_base_url (from config.db)
  v
Immich
```

For real users, run MediaRefinery behind HTTPS and persist `/data`.
