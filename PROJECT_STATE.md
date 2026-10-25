# Project State - AI Video Prep Studio

Live snapshot for Process Mentor and planning sessions. Update when the active branch,
milestone status, blockers, or next step changes.

---

## Updated

2026-10-25

## Current State

| Field | Value |
|---|---|
| Active work | ADR 006 pivot (crv frames, real transcription, URL intake, package v2) — light mode |
| Main branch | M001–M009 + ADR 006 docs (PR #23, #24) |
| Pivot branches | stacked: gpu-worker → real-transcription → crv-frames → url-intake → llm-package → ui-url-form (merged via PRs #25+) |
| Status | 353 tests green; Docker E2E passed (URL 51 min → 7 min, file → 12 min); security review fixed |
| Blockers | None. Risk: Docker Desktop disk lives on C: (≈5 GB free) |

## Done (ADR 006)

- [x] GPU worker (faster-whisper turbo, CUDA wheels, nvidia runtime), one shared image, non-root
- [x] Limits 3 h / 5 GiB; data on `DATA_DIR` (A:), HF cache mount
- [x] Real transcription: author's manual subtitles → else whisper with hotwords (glossary + terms + title)
- [x] Scene-change frames via crv (pinned git SHA, 2 isolated patches, canary), ffmpeg 20 s fallback
- [x] URL intake (any yt-dlp site): SSRF guard at connect time, fake-IP proxy range configurable
- [x] Package v2: README.md for the agent, transcript.md with chapters + inline frames, failed_parts gaps
- [x] UI (RU): file / link tabs, language, terms, live stage
- [x] CSRF (full Origin + Sec-Fetch-Site), TrustedHost, ports only 127.0.0.1:8000
- [x] Security review (clean context): 0 Critical/High; 2 Medium + 5 Low fixed

## Next (optional)

- Move Docker Desktop disk image to A: (Settings → Resources → Advanced) — C: is nearly full.
- Frames are the slowest stage (CPU decode, ~5 min per hour of 720p): NVDEC proxy if needed.
- `datetime.utcnow()` deprecation warnings (pre-existing debt).

## Process Notes

- Read this file first at the start of planning or implementation sessions.
- Do not include this file in clean Codex Reviewer or Security Agent prompts.
- Keep this file short; detailed history belongs in `AI_WORKLOG.md`.
