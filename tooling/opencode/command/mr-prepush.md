---
description: Verify PR readiness with local QA and branch policy.
agent: mr-qa-gatekeeper
---

Check whether the repo is ready to open or update a PR to `dev`.

Steps:
- Confirm the branch with `git status --short --branch`.
- Confirm the current branch is an `mr-` feature branch (not `dev` or `main`) unless `$ARGUMENTS` explicitly documents an exception.
- Run the full `/mr-qa` local QA sequence.
- Report whether pushing the feature branch and opening/updating a PR is allowed; list blockers.
- When checking remote CI, use `gh --repo automationnexus/MediaRefinery` if outside the clone; use `gh run view <id> --log-failed` for failed runs.
- Remind that direct pushes to `dev` and `main` are forbidden.

Do not edit files, push, or open PRs.
