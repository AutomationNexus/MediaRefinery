---
description: Follow the CI-gated dev-to-main release workflow.
argument-hint: [optional notes]
---

Follow the MediaRefinery release workflow: $ARGUMENTS

Confirm local branch/status. Ensure local QA passed or run `/qa` now. Ensure latest `dev`
has green CI (`gh run list --repo automationnexus/MediaRefinery --branch dev --limit 5`,
`gh run view <id> --log-failed` for failures). Promote `dev` to `main` only through the
**Promote dev to main** GitHub Actions workflow unless the user explicitly approves the
documented manual fallback. Tag only when the user requests it. Never push directly to
`dev`/`main`.
