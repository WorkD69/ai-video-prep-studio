# AI Video Prep Studio — CLAUDE.md

## What This Project Does

Web service that converts a long video into an LLM-ready ZIP package containing:
- Full transcript with global timecodes (Markdown + JSON)
- Lecture summary input document
- Screenshots manifest CSV
- Failed/silent parts report
- Metadata JSON
- Screenshot frames (every 20 seconds)

## Stack (Locked)

| Layer | Technology |
|---|---|
| Backend API | FastAPI |
| Job queue | RQ (Redis Queue) |
| Message broker / cache | Redis |
| Database | PostgreSQL |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| Frontend | Jinja2 + HTMX + TailwindCSS (CDN) |
| Transcription | faster-whisper (local) |
| Video processing | ffmpeg + ffprobe (subprocess) |
| Logging | structlog (always include `job_id` + `stage`) |
| CI | GitHub Actions (mock transcription in tests) |
| Deploy | Docker + docker-compose |

## MVP Hard Limits

- Max file size: 500 MB
- Max video duration: 60 minutes
- Screenshot interval: 20 seconds
- Max active jobs: 1 per session/IP
- File retention: 24 hours, then auto-cleanup

## Project Structure (Target)

```
app/
  api/          # FastAPI routes
  workers/      # RQ job handlers
  pipeline/     # Video + transcription logic
  models/       # SQLAlchemy models
  schemas/      # Pydantic schemas
  templates/    # Jinja2 HTML
  static/       # CSS, JS (minimal)
docs/
  adr/          # Architecture Decision Records
  agents/       # Agent cards for AI assistants
tests/
alembic/
docker-compose.yml
Dockerfile
```

---

## Enterprise Vibe Coding Rules

These rules govern all AI-assisted work on this project. Every agent, every session.

**Rule 1 — AI is a tool, not a decision maker.**
AI suggests; human approves. No merges without human sign-off.

**Rule 2 — Plan before code.**
No implementation session without a written spec or task description. No spec = no code.

**Rule 3 — One chat = one task.**
Fresh context for each feature. Do not mix features in a single conversation.

**Rule 4 — One branch = one feature.**
Create a feature branch before starting. Never work directly on `main`.

**Rule 5 — Max 2 debug iterations in one chat.**
If a bug is not fixed after 2 attempts, open a new context with fresh state.

**Rule 6 — Review in clean context.**
Use Codex Reviewer (see AGENTS.md) with a clean context window: provide only the diff + acceptance criteria.

**Rule 7 — No copy-paste from external repos without license check.**
Before using any external code, verify license compatibility (MIT/Apache preferred).

**Rule 8 — Quality gates required before merge.**

| Gate | When Required |
|---|---|
| Functional (manual test) | Always |
| Tests (pytest green) | Always |
| Security (upload/subprocess paths) | Always |
| Human Understanding (reviewer can explain the code) | Always |
| Performance (hot paths only) | Contextual |
| Reliability (external service integrations) | Contextual |
| Documentation (interface changes) | Contextual |

---

## Key Interfaces (Do Not Change Without ADR)

- `Transcriber` → `FasterWhisperTranscriber` / `MockTranscriber`
- `no_speech_prob` silence threshold: `0.6`
- ZIP output filename: `llm_analysis_package_<safe_stem>_<YYYYMMDD_HHMMSS>.zip`
- Screenshot naming: `frame_NNNNNNs.jpg` (zero-padded seconds)
- Global timecode formula: `global = chunk_offset + local`
