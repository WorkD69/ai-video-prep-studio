# AI Worklog — AI Video Prep Studio

Format: `[YYYY-MM-DD] [Type] Description`
Types: DECISION, IMPL, REVIEW, FIX, DEPLOY, NOTE

---

## Log

### 2026-06-01 — Milestone 002B Upload Intake Implementation

**[IMPL] Implemented upload intake and job creation.**
Added `POST /jobs/upload` and `GET /jobs/{job_id}` with UUID-based storage filenames,
server-generated anonymous `session_id`, `status = pending`, `expires_at = created_at + 24h`,
and no RQ/worker/media-processing scope.

**[FIX] Implementation hardening before review.**
Process Mentor caught an initial-size edge case in streaming upload size enforcement.
Added `test_upload_too_large_in_initial_header` and ensured `stream_save()` rejects before
creating a file when the first 8-byte header already exceeds the configured limit.

**[REVIEW] Security Agent verdict: SECURITY ACCEPT WITH CHANGES (Low only).**
Fixed all Low findings before clean review:
- removed dead `MAX_FILE_SIZE_MB` / `max_file_size_mb` config,
- replaced raw DB exception logging with `error_type=type(e).__name__`,
- removed raw `max_upload_bytes` from 413 HTTP responses,
- synced the milestone logging example with the safer logging pattern.

**[REVIEW] Codex Reviewer verdict: ACCEPT.**
No blocking issues found. Residual `datetime.utcnow()` deprecation warnings are known and
deferred to a separate cleanup because existing SQLAlchemy models already use that pattern.

**[NOTE] Gates passed.**
`pytest tests/ -v` passed with 15 tests. Docker gate passed:
Alembic upgrade, `docker compose up -d --build app`, `/health`, manual upload smoke,
and `GET /jobs/{job_id}` all succeeded.

**[LEARNING] Upload milestones need atomicity and edge-size tests.**
For future upload/file milestones, include tests for DB failure after file save, partial-file
cleanup on 413, and size rejection before writing when the initial header exceeds the limit.

---

### 2026-05-29 — Process Learning Patch: Milestone 001 Retrospective

**[NOTE] Milestone 001 completed: spec → impl → gates → reviewer → fixes → ACCEPT → merge.**

**[REVIEW] Reviewer findings summary:**
- `.dockerignore` missing (Medium) — fixed
- Port strategy inconsistency (Medium) — fixed
- UUID contract not documented (Medium) — fixed
- Docker canonical gate was implicit — now documented in CLAUDE.md Process Rules

**[NOTE] Windows host psycopg2/cp1251 failure.**
Host-native DB verification fails on Windows with cp1251 locale.
Prevention: Docker canonical gate is now the required DB verification method for all milestones.

**[LEARNING] Agent mistakes must become rules or gates.**
When an agent error or environment failure is discovered during review,
do not only fix the output — update one of: rule / test / checklist / agent card / quality gate.
This entry triggered the Process Rules section added to CLAUDE.md.

**[LEARNING] Reviewer fix loop needs a hard limit.**
Fix cycles extended due to Low/Optional findings without a stop rule.
Prevention: Max 2 reviewer-fix cycles per milestone. Low/Optional do not block merge if gates green.

---

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

### 2026-05-28 — Milestone 001 Reviewer Fixes

**[NOTE] Windows host psycopg2 / UnicodeDecodeError under cp1251 locale:**
psycopg2-binary on Windows host fails with UnicodeDecodeError when connecting to PostgreSQL
under a cp1251 system locale. Docker container verification is unaffected.
Canonical DB verification gate for this project is Docker-based:
```
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app
Invoke-RestMethod http://localhost:8000/health
```
Do not attempt host-native psycopg2 DB verification on Windows with a non-UTF-8 locale.

**[DECISION] UUID generated by application, not DB.**
DB-side `gen_random_uuid()` / PostgreSQL extensions not used in MVP.
UUIDs are generated by SQLAlchemy via Python `uuid.uuid4`. Updated milestone spec accordingly.

---

### 2026-05-28 — Schema-Contract Sync (Codex Process Mentor Review)

**[REVIEW] Codex Process Mentor verdict: PROCEED WITH CAUTION.**
Schema-contract mismatch identified before Phase B implementation starts.

**[FIX] Added `input_path` / `output_path` to jobs contract across all docs.**
`input_path` (VARCHAR NOT NULL) — path to the saved input video file.
`output_path` (VARCHAR nullable) — path to the finished ZIP package after processing.
Required for future upload/worker/download lifecycle. Added to:
- docs/milestones/001-db-schema-project-skeleton.md (In Scope, DB Schema Reference, Acceptance Criteria)
- docs/agents/backend-agent.md (jobs table schema)
- docs/ARCHITECTURE.md (PostgreSQL diagram block — also synced users + usage_log shape)

**[NOTE] Phase B (implementation) not started. All changes are docs-only.**

---

### 2026-06-02 — Milestone 003 RQ Worker + Mock Processing

**[IMPL] Implemented RQ enqueue + mock worker lifecycle.**
- `app/config.py`: added 5 optional settings (`rq_queue_name`, `rq_job_timeout`,
  `mock_processing_delay_seconds`, `mock_force_fail`, `mock_failure_trigger_enabled`).
  All default to safe production values (failure triggers off).
- `app/redis_client.py`: added `get_queue()` helper returning `rq.Queue` over `redis_url`.
- `app/workers/process_job.py` (new): `process_job(job_id)` with guarded transitions
  `pending|queued -> processing -> done|failed`, idempotency, filename-based + global
  failure triggers, re-raise for RQ failed registry, isolated `SessionLocal` session.
- `app/api/jobs.py`: enqueue after initial commit; guarded flip `pending -> queued`;
  re-read on race (flip 0 rows); compensation `pending -> failed` + HTTP 503 on enqueue failure.
- `docker-compose.yml`: added `volumes: ./uploads:/app/uploads` + `./outputs:/app/outputs`
  to `worker` service (service itself already existed from prior milestone).
- `tests/test_worker.py` (new): 10 worker tests covering happy path, idempotency,
  guarded noop on wrong status, both failure triggers, production safety of filename trigger.
- `tests/test_upload.py`: added `execute()` support to `MockDB`; updated all upload fixtures
  to mock `get_queue`; updated `test_upload_happy_path` to expect `queued` (was `pending`);
  added 3 enqueue tests (happy, 503, race re-read).

**[NOTE] Gates passed.**
`pytest tests/ -v` passed with 28 tests (was 15 + 4 health). Docker canonical gate passed:
`docker compose run --rm app python -m alembic upgrade head`, `docker compose up -d app`,
and `Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json` returned
`status=ok`, `db=ok`, and `redis=ok`. All `datetime.utcnow()` deprecation warnings are
pre-existing pattern, deferred.

**[NOTE] Residual risk accepted.**
Orphan `pending` on crash between `db.commit()` and `enqueue()` — documented in spec,
reaper out of scope M003. `output_path=NULL` on `done` mock jobs — expected, no artifact yet.

---

### 2026-06-04 — Milestone 004 Media Pipeline Output Assembly

**[IMPL] Implemented MockTranscriber-backed ZIP output pipeline.**
- `app/pipeline/transcriber.py`: added ADR 003 `TranscriptSegment`, `Transcriber`, and locked
  `MockTranscriber` 5-segment fixture.
- `app/pipeline/timecodes.py` and `app/pipeline/silence.py`: added global timecode formatting,
  active/silent classification, and deterministic failed/silent reason strings.
- `app/pipeline/output_assembly.py`: added transcript MD/JSON, lecture summary input,
  screenshots manifest CSV, failed/silent report, and metadata JSON builders.
- `app/pipeline/zip_packaging.py`: added safe ZIP stem sanitization, fixed archive entries,
  placeholder screenshot files, and partial-ZIP cleanup on failure.
- `app/pipeline/mock_pipeline.py`: added per-job staging orchestration with `try/finally` cleanup
  and ZIP generation under `outputs/`.
- `app/workers/process_job.py`: replaced sleep-only mock success with `run_mock_output_pipeline(job)`
  and guarded `done` update that persists `output_path`.
- `app/config.py`: added `output_dir`.
- Tests added under `tests/unit/` and `tests/integration/`; worker tests now cover `output_path`
  success and pipeline failure.

**[NOTE] Gates passed.**
`python -m pytest tests/ -v --tb=short` passed with 126 tests. Docker canonical gate passed after
rebuilding app/worker images: `docker compose run --rm app python -m alembic upgrade head`,
`docker compose up -d --build app worker`, and `/health` returned `status=ok`, `db=ok`,
`redis=ok`. Manual smoke upload reached `done`; DB `output_path` was set to
`/app/outputs/llm_analysis_package_m004-smoke_20260604_120700.zip`; ZIP entries and screenshot
count invariants were verified; per-job staging cleanup left no files behind.

**[NOTE] Residual risks accepted.**
M004 still uses placeholder screenshots and MockTranscriber only. Real ffmpeg/ffprobe,
faster-whisper, download endpoint, UI, 1-active-job limit, and 24h cleanup remain out of scope.
Existing `datetime.utcnow()` deprecation warnings remain deferred.

---

### 2026-06-06 - Milestone 005 Download Endpoint

**[IMPL] Implemented root-mounted ZIP download endpoint.**
- `app/api/download.py`: added unprefixed `GET /download/{job_id}` route returning `FileResponse`
  for done, non-expired jobs with existing ZIP artifacts.
- `app/main.py`: included the unprefixed download router so the route is exactly
  `/download/{job_id}`, not `/jobs/download/{job_id}`.
- `tests/test_download.py`: added 12 deterministic route tests covering happy path, 404,
  all non-done statuses, expired jobs, null `output_path`, traversal defense, missing file,
  invalid UUID, and safe `Content-Disposition`.

**[NOTE] Gates passed.**
TDD red state confirmed first: all 12 download tests failed with 404 before route implementation.
After implementation, `python -m pytest tests/test_download.py -q --tb=short` passed with
12 tests, and `python -m pytest tests/ -q --tb=short` passed with 138 tests.
Docker canonical gate passed: `docker compose run --rm app python -m alembic upgrade head`,
`docker compose up -d --build app worker`, and `/health` returned `status=ok`, `db=ok`,
and `redis=ok`. Manual smoke verified 404 unknown job, 422 invalid UUID, deterministic 409
with worker stopped, 200 download for a completed job, safe `Content-Disposition`, and required
ZIP entries.

**[NOTE] Residual risks accepted for now.**
The implementation follows the existing naive `datetime.utcnow()` pattern documented as deferred
technical debt. Download corruption/invariant cases are covered by pytest.

**[REVIEW] Clean gates passed and PR #13 merged.**
Security Agent returned `SECURITY APPROVED - no security issues found`. Codex Reviewer returned
`ACCEPT - no issues found` and re-ran `python -m pytest tests/test_download.py -q --tb=short`
with 12 passing tests. GitHub Actions `pytest` succeeded on PR #13. PR #13 was merged into
`main` on 2026-06-06.

<!-- Add new entries above this line, newest first within each date block -->
