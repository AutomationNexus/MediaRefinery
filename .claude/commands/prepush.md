---
description: Verify PR readiness with local QA and branch policy.
---

Confirm the current branch is an `mr-` feature branch (not `dev`/`main`) unless explicitly
documented as an exception. Run the full `/qa` sequence via `qa-gatekeeper`. When checking
remote CI, use `gh --repo automationnexus/MediaRefinery` if outside the clone; use
`gh run view <id> --log-failed` for failed runs. Report whether pushing/opening a PR is
allowed, with blockers. Direct pushes to `dev`/`main` are forbidden. Do not edit files,
push, or open PRs.
