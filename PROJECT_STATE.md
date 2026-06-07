# Project State - AI Video Prep Studio

Live snapshot for Process Mentor and planning sessions. Update when the active branch,
milestone status, blockers, or next step changes.

---

## Updated

2026-06-07

## Current State

| Field | Value |
|---|---|
| Active branch | `main` |
| Main branch | `main` includes M001-M007 plus ADR 004 / M008 spec (latest: PR #20 docs) |
| Current task | Start M008 implementation: 1 active job per signed-cookie session |
| Status | M008 ADR/spec merged; ready for clean Backend Implementation Agent |
| Blockers | None |

## Recently Completed

| Milestone / Task | PR | Status |
|---|---:|---|
| M001 - DB schema + project skeleton | #1 | Done |
| M002 - Upload intake + job creation | #2, #3, #4 | Done |
| M003 - RQ worker mock processing | #5, #6 | Done |
| M004 - Media pipeline output assembly | #7, #8 | Done |
| M005 spec - Download endpoint | #9 | Done |
| Process state files | #10, #11 | Done |
| GitHub Actions CI | #12 | Done |
| M005 implementation - Download endpoint | #13 | Done |
| M006 spec - Job status page | #15 | Done |
| M006 implementation - Job status page | #16 | Done |
| M007 spec - File retention + cleanup | #18 | Done |
| M007 implementation - File retention + cleanup | #19 | Done |
| ADR 004 + M008 spec - Session active-job limit | #20 | Done |

## MVP 1 Remaining Scope

- [x] GitHub Actions CI
- [x] M005 implementation: `GET /download/{job_id}`
- [x] M006: Job status page with HTMX polling
- [x] M007: 24h file retention + cleanup
- [ ] M008 implementation: 1 active job per signed-cookie session
- [ ] Real ffmpeg screenshots
- [ ] Real faster-whisper transcription
- [ ] Docker deploy hardening

## Recommended Next Action

Implement M008 from `docs/milestones/008-session-active-job-limit.md`.

Use a clean Backend Implementation Agent chat on a new branch:
`feature/milestone-008-session-limit`.

ADR 004 is accepted and merged. The M008 session mechanism is a signed browser cookie, not IP /
`X-Forwarded-For`.

## Known Technical Debt

- `datetime.utcnow()` deprecation warnings remain deferred.
- M004 uses mock transcription and mock screenshots; real media processing remains deferred.
- Inline HTMX 429 upload error display is in scope for M008.

## Process Notes

- Read this file first at the start of planning or implementation sessions.
- Do not include this file in clean Codex Reviewer or Security Agent prompts.
- Keep this file short; detailed history belongs in `AI_WORKLOG.md`.
