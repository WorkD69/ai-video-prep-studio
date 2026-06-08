# Project State - AI Video Prep Studio

Live snapshot for Process Mentor and planning sessions. Update when the active branch,
milestone status, blockers, or next step changes.

---

## Updated

2026-06-08

## Current State

| Field | Value |
|---|---|
| Active branch | `main` |
| Main branch | `main` includes M001-M008 (latest: PR #21 M008 implementation) |
| Current task | Post-M008 state sync; choose/spec next MVP media milestone |
| Status | M008 merged; CI, Security, Reviewer, and Docker/manual gates passed |
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
| M008 implementation - Session active-job limit | #21 | Done |

## MVP 1 Remaining Scope

- [x] GitHub Actions CI
- [x] M005 implementation: `GET /download/{job_id}`
- [x] M006: Job status page with HTMX polling
- [x] M007: 24h file retention + cleanup
- [x] M008 implementation: 1 active job per signed-cookie session
- [ ] Real ffmpeg screenshots
- [ ] Real faster-whisper transcription
- [ ] Docker deploy hardening

## Recommended Next Action

Choose and spec the next MVP media milestone. Recommended next candidate: real ffmpeg/ffprobe
media probing + screenshot extraction, because it unlocks duration enforcement and replaces the
remaining placeholder screenshot path while keeping faster-whisper as a separate later milestone.

## Known Technical Debt

- `datetime.utcnow()` deprecation warnings remain deferred.
- M004 still uses mock transcription and placeholder screenshots; real media processing remains deferred.
- Duration limit enforcement still depends on real ffprobe.

## Process Notes

- Read this file first at the start of planning or implementation sessions.
- Do not include this file in clean Codex Reviewer or Security Agent prompts.
- Keep this file short; detailed history belongs in `AI_WORKLOG.md`.
