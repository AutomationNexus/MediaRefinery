---
description: Follow the CI-gated dev-to-main release workflow.
argument-hint: [optional notes]
---

Run the release workflow: $ARGUMENTS

Confirm local QA passed or run `/qa`. Ensure the latest `dev` on GitHub has green CI.
Promote `dev` to `main` only via the **Promote dev to main** GitHub Actions workflow
(choose `bump-type` per the change: `patch`/`minor`/`major` — versioned repos only) —
never push `dev`/`main` directly. Tag only when the user explicitly requests it.

<!-- repo-specific -->

Check `dev` CI status with `gh run list --repo automationnexus/MediaRefinery --branch dev
--limit 5`; use `gh run view <id> --log-failed` for failures. Never push directly to
`dev`/`main`; the documented manual fallback requires explicit user approval.
