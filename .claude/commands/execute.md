---
description: Execute an approved plan through the expert subagents (implement, QA, review, optional PR).
argument-hint: [optional focus notes]
---

Run the build pipeline for an approved plan: $ARGUMENTS

0. Classify the change into a risk track (`CLAUDE.md`'s "Risk tracks" — defaults below
   if this repo hasn't customized them) before dispatching anything:
   - **Track A — trivial/direct** (one file or docs-only, no behavioral/security/
     release/shared-contract impact): implement directly in the main session, run
     `qa-gatekeeper`, self-review, `/prepush`, PR. Skip steps 2 and 4 below.
   - **Track B — standard behavior change** (one domain): dispatch `architect` only if
     boundaries are unclear, then run steps 1–6 in order.
   - **Track C — multi-domain/high-risk**: dispatch `architect` first to partition the
     work; run domain engineers in parallel only for genuinely disjoint, non-shared
     paths (serialize or have the main session own any shared/contract file); then
     steps 3–6, plus a mandatory `security-auditor` pass if any trigger below applies.
   - **Track D — release/promote**: do not run this pipeline. Confirm `dev` CI is
     green, run the security/release readiness gate, then use `/release` or the org
     root's `/promote` with explicit human confirmation.
1. Confirm you are on a feature branch off `dev` (never `dev`/`main` directly), using
   the repo's branch prefix from `CLAUDE.md`.
2. Dispatch the repo's domain engineer agent(s) for the implementation (see the
   CLAUDE.md subagent table).
3. Dispatch `qa-gatekeeper` for the local QA gate.
4. Dispatch `reviewer` for independent review. If the change touches workflows,
   dependencies, permissions, `.claude/settings.json`, authentication/session/
   credential handling, deploy/release scripts, secret-adjacent paths, or
   Docker/build-context files, also dispatch `security-auditor` — do not skip this for
   a Track B change just because it's single-domain.
5. Stop and report on the first failed gate — do not continue past a failure. If
   `reviewer`/`security-auditor` request fixes, apply them, then **rerun the QA gate**
   before proceeding — a QA pass recorded before a post-review fix is invalidated by
   that fix and does not carry forward.
6. Push the feature branch and open a PR to `dev`.

For hard cross-module conflicts or disagreement between subagents, escalate by
switching the main session to opus (`/model opus` or `opusplan`) rather than a
dedicated solver agent.

<!-- repo-specific -->

MediaRefinery: use branch prefix `mr-`. Domain engineers are `backend-engineer`
(`src/mediarefinery/`) and `frontend-engineer` (`frontend/`) — dispatch whichever
matches the plan's scope (both if it spans layers).
