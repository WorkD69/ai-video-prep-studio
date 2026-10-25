# AI Handoff - AI Video Prep Studio

Current session handoff. Overwrite this file before closing a session; do not append old
history.

---

## Last Updated

2026-06-11

## Role / Process Context

This session was the **Media Pipeline / Backend Implementation Agent** for M009. The next chats
should be: (1) clean **Security Agent** review, (2) clean **Codex Reviewer** — both with diff +
acceptance criteria only. Respond to Artem in Russian.

Project rules:
- `CLAUDE.md` is the source of stack, quality gates, and Enterprise Vibe Coding rules.
- `AGENTS.md` defines process roles.
- `PROJECT_STATE.md` and `AI_HANDOFF.md` are planning context only.
- Do not include `PROJECT_STATE.md` or `AI_HANDOFF.md` in clean Security Agent or Codex Reviewer prompts.
- Do not commit or push without explicit human action.

---

## Current Branch

`feature/milestone-009-ffmpeg-probing-screenshots` — uncommitted working tree changes (M009
implementation). NOT committed, NOT pushed.

## What This Session Completed (M009 implementation, TDD)

- New `app/pipeline/media.py`: `MediaError`, `MediaProber`/`ScreenshotExtractor` Protocols,
  `FfprobeMediaProber`, `FfmpegScreenshotExtractor`, `FakeMediaProber`, `FakeScreenshotExtractor`,
  `screenshot_timestamps(span)` (single source of truth), `get_media_prober()`, `get_media_backend()`.
- New `app/pipeline/media_pipeline.py`: `run_media_pipeline(job, prober, extractor)` — input path
  validated under `upload_dir`, real span, shared timestamps → extractor + manifest, MockTranscriber,
  `build_zip(screenshots_dir=...)`, try/finally staging cleanup.
- `app/api/jobs.py`: upload-time ffprobe (MediaError → 400, > 3600 → 422, == 3600 allowed); file
  deleted before any job row; `duration_seconds` persisted; `_upload_reject_response` (HX fragment /
  JSON). New partial `upload_error_message.html`; `hx-target-400`/`hx-target-422` on the form.
- `build_zip` gained `screenshots_dir: Path | None = None` with invariant guard (M004 path intact).
- Worker calls `run_media_pipeline` with `get_media_backend()`. Config: `max_duration_seconds=3600`,
  `media_backend="ffmpeg"`. Dockerfile installs ffmpeg. CI sets `MEDIA_BACKEND=fake`.
- `docs/agents/media-pipeline-agent.md` updated (real producer, enforcement, backend selection).
- Tests: `tests/unit/test_media.py`, `tests/integration/test_media_pipeline.py`,
  `tests/test_upload_duration.py`, `tests/integration/test_media_smoke.py` (guarded). Patch targets
  updated in `test_worker.py`/`test_cleanup.py`; `FakeSettings` in upload/limit/pages tests gained
  `max_duration_seconds`; conftest sets `MEDIA_BACKEND=fake`.

## Key Implementation Deviation (documented)

`fps=1/20` alone drops the final 20 s slot on real ffmpeg (65 s → 3 frames, no `frame_000060s`;
reproduced on ffmpeg 8.1 host and 7.1.4 container, 1 fps and 25 fps sources). The filter is
**`fps=1/20:eof_action=pass`** — the spec's open question #2 anticipated confirming the mapping via
the real-binary smoke. Extra trailing frames are dropped by the spec'd reconciliation.

## Gates Already Passed

- `python -m pytest tests/ -q` → **254 passed** (includes 3 real-binary smoke tests; host has ffmpeg).
- `git diff --check` → clean.
- Docker gate: `alembic upgrade head` OK; `up -d --build app worker` OK; `/health` status=ok/db=ok/
  redis=ok; `ffprobe -version` (7.1.4) in container; in-container smoke 3 passed.
- Manual smoke: 65 s testsrc → done, ZIP with 4 real JPEGs incl. `frame_000060s`, duration 65.0,
  invariant 4==4==4; `toolong.mp4` (ffprobe-confirmed 3660 s) → 422 JSON + 422 HX fragment, file
  deleted, no job; corrupt ftyp file → 400, file deleted, no job.

## Exact Current Stop Point

Implementation + local/Docker gates complete. Awaiting: clean Security Agent review, clean Codex
Reviewer (prompts prepared in `tmp/review/` — see below), human sign-off, then commit/push/PR.

## Recommended Next Action

1. Open a clean Security Agent chat with `tmp/review/security_agent_prompt.md` (diff + security context).
2. Open a clean Codex Reviewer with `tmp/review/codex_reviewer_prompt.md` (diff + AC1-AC19).
3. Max 2 reviewer-fix cycles; then human sign-off → commit → push → PR into `main`.

## Open Notes

- `datetime.utcnow()` warnings remain deferred project-wide technical debt.
- M009 intentional inconsistency until M010: mock transcript (0-75 s) vs real screenshot span.
- `app/pipeline/mock_pipeline.py` no longer called by the worker; kept with its tests (M010 decides).
- Smoke fixtures live in `tmp/smoke/` (gitignored).
