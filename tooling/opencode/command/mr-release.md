---
description: Follow the CI-gated dev-to-main release workflow.
agent: build
---

Follow the MediaRefinery release workflow.

Steps:
- Confirm local branch and status.
- Ensure local QA has passed or run `/mr-qa` now.
- Ensure latest `dev` on GitHub has green CI (from merged PRs, not direct pushes). Use `gh run list --repo automationnexus/MediaRefinery --branch dev --limit 5` and `gh run view <id> --log-failed` for failures.
- Promote `dev` to `main` only through the **Promote dev to main** GitHub Actions workflow unless the user explicitly approves the documented manual fallback.
- After main CI passes, tag only when the user requests a tag.

Never push directly to `dev` or `main`. Report release state and blockers. Arguments: `$ARGUMENTS`.
