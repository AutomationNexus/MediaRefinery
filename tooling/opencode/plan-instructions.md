# MediaRefinery Plan Mode

- Stay read-only in plan mode. Do not edit files.
- Never commit on `dev` or `main`. Plan work for a feature branch from `dev` with `mr-` prefix (see `docs/runbooks/branch-policy.md`).
- For API design, Immich integration, scan pipeline, models, or service architecture, invoke `@mr-architect` before finalizing the plan.
- When the user approves the plan and says go, build, or execute, hand off to `/mr-execute` (built-in `build`). Do not implement inline.
