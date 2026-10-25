# Project State - AI Video Prep Studio

Live snapshot for Process Mentor and planning sessions. Update when the active branch,
milestone status, blockers, or next step changes.

---

## Updated

2026-06-11

## Current State

| Field | Value |
|---|---|
| Active branch | `feature/milestone-009-ffmpeg-probing-screenshots` |
| Main branch | `main` includes M001-M008 plus ADR 005 / M009 spec (latest: PR #22 docs) |
| Current task | M009 implemented; pending clean Security Agent + Codex Reviewer, then human merge |
| Status | All tests green (254 passed); Docker/manual gate passed; NOT committed/pushed |
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
| ADR 005 + M009 spec - ffmpeg/ffprobe probing + screenshots | #22 | Done |

## MVP 1 Remaining Scope

- [x] GitHub Actions CI
- [x] M005 implementation: `GET /download/{job_id}`
- [x] M006: Job status page with HTMX polling
- [x] M007: 24h file retention + cleanup
- [x] M008 implementation: 1 active job per signed-cookie session
- [x] Real ffmpeg screenshots (M009 — implemented on branch, pending review/merge)
- [ ] Real faster-whisper transcription
- [ ] Docker deploy hardening

## Recommended Next Action

Run the M009 review gates in clean contexts: Security Agent (subprocess + path handling) and Codex
Reviewer (diff + AC1-AC19 only — no state files). After both pass and human sign-off, commit, push,
and open the PR. faster-whisper stays M010, deploy hardening M011.

## Known Technical Debt

- `datetime.utcnow()` deprecation warnings remain deferred.
- M009 intentional inconsistency until M010: transcript is the mock 0-75 s fixture while
  screenshots/`duration_seconds` span the real duration (documented in the spec).
- `app/pipeline/mock_pipeline.py` is no longer called by the worker (M009 uses
  `run_media_pipeline`); kept with its tests until M010 decides its fate.

## Process Notes

- Read this file first at the start of planning or implementation sessions.
- Do not include this file in clean Codex Reviewer or Security Agent prompts.
- Keep this file short; detailed history belongs in `AI_WORKLOG.md`.
