# AI Video Prep Studio — CLAUDE.md

## What This Project Does

Local web service that converts a video (uploaded file or a link to any yt-dlp site) into an
LLM-ready ZIP package (layout: ADR 006):
- `README.md` for the agent
- `transcript.md` — chapters + timecoded segments with frames inline
- `transcript.json`, `frames_manifest.csv`, `failed_parts.md`, `metadata.json`
- `frames/` — scene-change keyframes (crv), fallback every 20 seconds
- `subtitles.<lang>.vtt` when the author's manual subtitles were used

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
| Transcription | faster-whisper `turbo` on GPU (local); author's manual subtitles preferred |
| Frame selection | claude-real-video (crv) library, pinned commit (ADR 006) |
| URL download | yt-dlp Python API |
| Video processing | ffmpeg + ffprobe (subprocess) |
| Logging | structlog (always include `job_id` + `stage`) |
| CI | GitHub Actions (mock transcription in tests) |
| Deploy | Docker + docker-compose |

## MVP Hard Limits

- Max file size: 5 GiB
- Max video duration: 3 hours
- Frames: scene-change selection, ~5/min, max 300, 1280 px wide (fallback: every 20 seconds)
- Max active jobs: 1 per signed cookie session (`aivps_session`); do not use IP / `X-Forwarded-For`
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

## Process Rules

**Agent Mistake Handling**
If an agent produces an incorrect result, do not just fix the output.
Create a learning entry in AI_WORKLOG.md and update one of: rule / test / checklist / agent card / quality gate.

**Reviewer Fix Loop Limit**
Max 2 reviewer-fix cycles per milestone.
After 2 cycles: stop, summarize, open fresh context, check in with Process Mentor or Architect.
Low/Optional findings must not block merge if all gates are green and no High/Critical issues remain.

**Canonical Gate — DB/Infrastructure Milestones**
```
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app
# /health must return: status=ok, db=ok, redis=ok
```
Do not run host-native DB verification on Windows with a non-UTF-8 locale.

**New Chat Policy**
Open a new Claude Code chat for: new feature, new milestone, role change, clean review, debug after 2 failed iterations.

**A/B Branches Policy**
Use only for genuine architectural uncertainty:
media pipeline architecture, video splitting strategy, large upload strategy, storage layout, frontend UX, smart screenshot extraction.
Do NOT use for: reviewer fixes, formatting, small docs edits.

**Skills Policy**
Do not create a Skill preemptively.
Create a Skill only if a procedure has repeated ≥2 times or will clearly repeat across milestones.

**MCP Policy**
Do not connect MCP preemptively.
MCP must reduce manual work or error risk.
Backlog: GitHub MCP, DB read-only MCP, docs/browser MCP — for later milestones.

**Light Mode (ADR 006 pivot)**
For the crv pivot (ADR 006) the project runs in light mode: one branch per stage, pytest green at
each stage, one clean security review at the end. No per-stage spec documents or double reviews.

**Language Preference**
Claude Code responds to the human in Russian.
File names, commands, code, and logs may remain in English.

---

## Key Interfaces (Do Not Change Without ADR)

- `Transcriber` → `FasterWhisperTranscriber` / `MockTranscriber`
- `FrameSelector` → `CrvFrameSelector` / `FakeFrameSelector` (fallback `FfmpegScreenshotExtractor`)
- Transcript source priority: author's manual subtitles (video language) → whisper; never auto-captions
- `no_speech_prob` silence threshold: `0.6`
- ZIP output filename: `llm_analysis_package_<safe_stem>_<YYYYMMDD_HHMMSS>.zip`
- Screenshot naming: `frame_NNNNNNs.jpg` (zero-padded seconds)
- Global timecode formula: `global = chunk_offset + local`
