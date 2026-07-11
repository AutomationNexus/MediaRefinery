---
description: Verify PR readiness with local QA and branch policy.
---

Confirm the current branch is a feature branch (never `dev`/`main`). Run the full
`/qa` sequence via `qa-gatekeeper`. Report whether pushing/opening a PR is allowed,
with blockers. Direct pushes to `dev`/`main` are forbidden. Do not edit files, push,
or open PRs.

<!-- repo-specific -->

Confirm an `mr-` feature branch specifically. Use `gh --repo automationnexus/MediaRefinery`
when checking remote CI from outside the clone; use `gh run view <id> --log-failed`
for failed runs.
