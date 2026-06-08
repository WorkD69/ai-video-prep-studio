# AI Handoff - AI Video Prep Studio

Current session handoff. Overwrite this file before closing a session; do not append old
history.

---

## Last Updated

2026-06-08

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

M001-M008 are complete and merged. The project is ready to choose/spec the next MVP media
milestone.

Latest merge details:
- PR: `#21 feat: Add session active job limit`
- Merge commit: `6962745`
- CI: GitHub Actions `pytest` passed
- Local `main` is at the PR #21 merge commit

## What PR #21 Completed

- Implemented signed-cookie sessions with stdlib HMAC-SHA256.
- Enforced 1 active job per signed-cookie session with PostgreSQL advisory xact lock.
- Added HTMX inline 429 and JSON 429 handling.
- Added Alembic partial index `ix_jobs_active_session`.
- Added session/job-limit tests.
- Updated backend-agent session contract.
- Security Agent approved, Codex Reviewer accepted after Low doc cleanup, Docker/manual gate passed.

## M008 Decisions Now Implemented

- Session mechanism: signed browser cookie `aivps_session`.
- Signing: Python stdlib HMAC-SHA256 only (`hmac`, `hashlib`, `base64`), no new dependency.
- Expiry: browser-side `Max-Age`; no server-side expired-cookie rejection.
- Limit key: signed-cookie session, not IP / `X-Forwarded-For`.
- Race safety: PostgreSQL transaction-level advisory lock.
- HTMX 429: inline fragment via `response-targets`.
- Schema: Alembic partial index for active jobs by session.
- Agent-card update: `docs/agents/backend-agent.md` completed.

## Exact Current Stop Point

M008 is complete and merged. State files need this post-merge sync committed, then the project should
enter planning for the next MVP media milestone.

## Recommended Next Action

Start an Architect / Planner chat to choose and spec the next milestone. Recommended next candidate:
real ffmpeg/ffprobe media probing + screenshot extraction. Keep faster-whisper transcription as a
separate milestone unless the planner finds a strong reason to combine them.

## Open Notes

- `datetime.utcnow()` warnings remain deferred project-wide technical debt.
- M004 still uses mock transcription/screenshots; real media processing remains deferred.
- Duration limit enforcement still depends on ffprobe.
- Old files from earlier smoke runs may still exist in `uploads/` / `outputs/`.
