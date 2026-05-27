# AI Worklog — AI Video Prep Studio

Format: `[YYYY-MM-DD] [Type] Description`
Types: DECISION, IMPL, REVIEW, FIX, DEPLOY, NOTE

---

## Log

### 2026-05-27 — Project Bootstrap

**[DECISION] Project started. Goal locked.**
AI Video Prep Studio: a web service that converts a long video into an LLM-ready ZIP package.

**[DECISION] Stack locked (see docs/adr/001-stack-choice.md):**
- FastAPI + RQ + Redis + PostgreSQL + SQLAlchemy 2.x + Alembic
- Frontend: Jinja2 + HTMX + TailwindCSS (CDN)
- Transcription: faster-whisper (local), Transcriber interface
- Video: ffmpeg + ffprobe via subprocess
- Logging: structlog with job_id + stage fields
- CI: GitHub Actions with mock transcription
- Deploy: Docker + docker-compose

**[DECISION] Queue backend locked (see docs/adr/002-queue-backend.md):**
RQ chosen over Celery. Rationale: simpler, Redis already required, sufficient for MVP (1 job/session).

**[DECISION] Transcription strategy locked (see docs/adr/003-transcription.md):**
- faster-whisper runs locally, no external API
- `Transcriber` interface for swappability
- `no_speech_prob` silence threshold: 0.6
- `MockTranscriber` uses deterministic 5-segment fixture for CI

**[DECISION] MVP limits locked:**
- Max file: 500 MB
- Max duration: 60 min
- Screenshot interval: 20 s
- Max active jobs: 1 per session/IP
- File retention: 24 h

**[DECISION] ZIP output format locked:**
```
llm_analysis_package_<safe_stem>_<YYYYMMDD_HHMMSS>.zip
├── transcript_full_global_timecodes.md
├── transcript_full_global_timecodes.json
├── lecture_summary_input.md
├── screenshots_manifest.csv
├── failed_or_silent_parts.md
├── metadata.json
└── screenshots/frame_000330s.jpg ...
```

**[NOTE] Foundation files created:**
CLAUDE.md, AGENTS.md, AI_WORKLOG.md, docs/ARCHITECTURE.md, docs/ROADMAP.md,
docs/MVP_DEFINITION_OF_DONE.md, docs/adr/001..003, docs/agents/backend|media-pipeline|qa|security.

**Status:** Pre-implementation. No production code exists yet.

---

### 2026-05-28 — Foundation Review: Small Changes

**[DECISION] screenshots_manifest.csv schema locked:**
```
frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id
```
Old column names (`timestamp_formatted`, `global_second`) deprecated and removed from all docs.

**[DECISION] Commercialization is a future product direction, not permanently out of scope.**
Future Commercial Phase added to ROADMAP.md. React/Next.js deferred, not forbidden.

**[NOTE] ruvector.db added to .gitignore (local AI tool index, not part of project).**

---

<!-- Add new entries above this line, newest first within each date block -->
