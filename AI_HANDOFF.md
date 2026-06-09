# AI Handoff - AI Video Prep Studio

Current session handoff. Overwrite this file before closing a session; do not append old
history.

---

## Last Updated

2026-06-09

## Role / Process Context

The next chat should start as a clean **Media Pipeline / Backend Implementation Agent** for M009,
or as Process Mentor if Artem wants one final process check first.
Respond to Artem in Russian.

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

M001-M008 are complete and merged. ADR 005 + M009 spec are also merged via PR #22. Implementation
has NOT started.

## What PR #22 Completed

- `docs/adr/005-media-processing-interface.md` — **Accepted**. Media-processing interface
  (`MediaProber` / `ScreenshotExtractor`, real ffprobe/ffmpeg + fake, selected by `MEDIA_BACKEND`)
  and the duration-enforcement decision.
- `docs/milestones/009-ffmpeg-probing-screenshots.md` — full M009 spec (AC1–AC19, test plan,
  security focus, Docker gate, implementation order, clean Implementation Agent prompt).
- Updated `PROJECT_STATE.md`, `AI_HANDOFF.md`, `AI_WORKLOG.md`.
- **No implementation code** — `app/`, `tests/`, `Dockerfile`, CI workflow untouched.

## M009 Locked Decisions (human-approved 2026-06-09)

- **D1** — 60-min limit enforced **at upload** via synchronous ffprobe (before enqueue); reject +
  delete file; `duration_seconds` written to the job. Reject: 422 (> 60 min), 400 (corrupt media).
  Exactly 3600 s allowed (strict `>`).
- **D2** — Real audio extraction (`ffmpeg -vn`) deferred to M010; M009 keeps `MockTranscriber`.
- **D3** — Screenshots via single-pass `ffmpeg -vf fps=1/20`, renamed to `frame_NNNNNNs.jpg`,
  reconciled to a shared `screenshot_timestamps(span)` helper (single source of truth → invariant
  holds by construction).
- **D4** — ADR 005 written (Accepted).
- Subprocess policy: list args, `shell=False`, validated path under `upload_dir`, timeouts,
  `capture_output`; **`--` not mandatory** (ffmpeg/ffprobe don't guarantee POSIX `--`; use only if
  Docker smoke confirms).
- HTMX reject: extend the `response-targets` contract with `hx-target-400`/`hx-target-422` →
  `#upload-error`, new shared partial `upload_error_message.html`; non-HX → JSON 400/422.
- CI stays binary-free via `MEDIA_BACKEND=fake`; one guarded real-binary smoke test (skipif no ffmpeg).
- Dockerfile gains ffmpeg (shared app+worker image); no new DB migration (`duration_seconds` exists).

## Exact Current Stop Point

M009 spec + ADR 005 are complete and merged. The project is ready for M009 implementation.

## Recommended Next Action

Implement M009 in a clean Media/Backend Implementation Agent chat on
`feature/milestone-009-ffmpeg-probing-screenshots`, following the spec's Implementation order and
Acceptance Criteria. Keep faster-whisper (M010) and deploy hardening (M011) separate.

## Open Notes

- `datetime.utcnow()` warnings remain deferred project-wide technical debt.
- M004 still uses mock transcription/placeholder screenshots; M009 makes screenshots + duration real,
  M010 makes transcription real.
- Old files from earlier smoke runs may still exist in `uploads/` / `outputs/`.
