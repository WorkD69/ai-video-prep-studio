# AI Handoff - AI Video Prep Studio

Current session handoff. Overwrite this file before closing a session; do not append old
history.

---

## Last Updated

2026-06-07

## Role / Process Context

The next chat should start as Codex Process Mentor / Tech Lead or Architect / Planner for
AI Video Prep Studio. Respond to Artem in Russian.

Project rules:
- `CLAUDE.md` is the source of stack, quality gates, and Enterprise Vibe Coding rules.
- `AGENTS.md` defines process roles.
- `PROJECT_STATE.md` and `AI_HANDOFF.md` are planning context only.
- Do not include `PROJECT_STATE.md` or `AI_HANDOFF.md` in clean Security Agent or Codex Reviewer prompts.
- Do not commit or push without explicit human action.

---

## Current Branch

`main`

## Current State

M007 implementation is complete and merged. ADR 004 + M008 spec are also merged.

Latest merge details:
- PR: `#20 docs: Add M008 session limit spec`
- Merge commit: `f61fcb1`
- CI: GitHub Actions `pytest` passed
- Local `main` is at the PR #20 merge commit

## What PR #20 Completed

- Added `docs/adr/004-session-mechanism.md` with Status: Accepted.
- Added `docs/milestones/008-session-active-job-limit.md`.
- Recorded M008 planning decisions in `AI_WORKLOG.md`.
- No implementation code was changed.

## M008 Decisions Now Fixed

- Session mechanism: signed browser cookie `aivps_session`.
- Signing: Python stdlib HMAC-SHA256 only (`hmac`, `hashlib`, `base64`), no new dependency.
- Expiry: browser-side `Max-Age`; no server-side expired-cookie rejection.
- Limit key: signed-cookie session, not IP / `X-Forwarded-For`.
- Race safety: PostgreSQL transaction-level advisory lock.
- HTMX 429: inline fragment via `response-targets`.
- Schema: Alembic partial index for active jobs by session.
- Agent-card update: `docs/agents/backend-agent.md` is in M008 implementation scope.

## Exact Current Stop Point

ADR 004 and M008 spec are complete and merged. The project is ready for M008 implementation.

Create a new implementation branch from `main`:
`feature/milestone-008-session-limit`.

## Recommended Next Action

Open a clean Backend Implementation Agent chat for M008. Give it only implementation context:
`CLAUDE.md`, `AGENTS.md`, ADR 004, M008 spec, relevant backend files, and the backend/QA/security
agent cards.

Do not include `PROJECT_STATE.md` or `AI_HANDOFF.md` in future clean Security Agent or Codex
Reviewer prompts.

## Open Notes

- `datetime.utcnow()` warnings remain deferred project-wide technical debt.
- M004 still uses mock transcription/screenshots; real media processing remains deferred.
- Old files from earlier smoke runs may still exist in `uploads/` / `outputs/`.
