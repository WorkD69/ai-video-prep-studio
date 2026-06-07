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
| Main branch | `main` includes M001-M007 (latest: PR #19 M007 implementation) |
| Current task | Post-M007 planning; choose and spec the next MVP milestone |
| Status | M007 merged; all implementation, security, reviewer, CI, and Docker/manual gates passed |
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

## MVP 1 Remaining Scope

- [x] GitHub Actions CI
- [x] M005 implementation: `GET /download/{job_id}`
- [x] M006: Job status page with HTMX polling
- [x] M007: 24h file retention + cleanup
- [ ] M008: 1 active job per session/IP
- [ ] Real ffmpeg screenshots
- [ ] Real faster-whisper transcription
- [ ] Docker deploy hardening

## Recommended Next Decision

Prepare M008: 1 active job per session/IP.

Before implementation, create ADR 004 for the session mechanism. The current `session_id` is a
fresh UUID per upload and is not tied to a browser cookie, IP policy, or signed token.

Likely next candidates:

1. M008: 1 active job per session/IP, with ADR 004 first.
2. Real ffmpeg screenshots.
3. Real faster-whisper transcription.
4. Docker deploy hardening.

## Known Technical Debt

- `datetime.utcnow()` deprecation warnings remain deferred.
- M004 uses mock transcription and mock screenshots; real media processing remains deferred.
- Inline HTMX upload error display remains deferred by the M006 spec.

## Process Notes

- Read this file first at the start of planning or implementation sessions.
- Do not include this file in clean Codex Reviewer or Security Agent prompts.
- Keep this file short; detailed history belongs in `AI_WORKLOG.md`.
