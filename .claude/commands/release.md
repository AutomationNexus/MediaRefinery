---
description: Follow the CI-gated dev-to-main release workflow.
argument-hint: [optional notes]
---

Run the release workflow: $ARGUMENTS

This is a Track D action (`CLAUDE.md`'s "Risk tracks") — it is never entered implicitly
from `/execute`.

Confirm local QA passed or run `/qa`. Ensure the latest `dev` on GitHub has green CI.
Dispatch `security-auditor` for a release-sensitive security sweep over everything
`dev` has that `main` doesn't (full checklist, not just the files in the last PR) —
this is mandatory for every promotion of a full repo, not conditional on a trigger
list. Do not proceed past an unresolved finding.
Promote `dev` to `main` only via the **Promote dev to main** GitHub Actions workflow
(choose `bump-type` per the change: `patch`/`minor`/`major` — versioned repos only) —
never push `dev`/`main` directly, and only after explicit human confirmation of the
dispatch (`human-confirmation-required` per the org root's `CLAUDE.md` "Unified
authority and confirmation"). Tag only when the user explicitly requests it.

<!-- repo-specific -->

Check `dev` CI status with `gh run list --repo automationnexus/MediaRefinery --branch dev
--limit 5`; use `gh run view <id> --log-failed` for failures. Never push directly to
`dev`/`main`; the documented manual fallback requires explicit user approval.
