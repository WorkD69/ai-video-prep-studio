# Milestone 002 — Upload Intake + Job Creation

## Summary

Implements file upload intake and job creation. After a successful upload, a `jobs` row is created with `status = pending`. No media processing, no RQ enqueue, no worker.

Full job lifecycle (`pending → queued → processing → done/failed`) is deferred to a future milestone.

---

## Branch

`feature/milestone-002-upload-intake`

---

## In Scope

- `POST /jobs/upload` — validate file, save to local storage, create job row
- `GET /jobs/{job_id}` — return job metadata
- Streaming file save (no full read into memory)
- File validation: MIME type, extension, magic bytes, size limit
- UUID-based storage filename
- `status = pending` on job creation
- Server-generated `session_id` (anonymous, metadata only)
- `video_size_bytes` populated from actual bytes written
- `original_filename` / `stored_filename` / `input_path` in DB
- `UPLOAD_DIR` and `MAX_UPLOAD_BYTES` config settings
- `python-multipart` in requirements if missing
- Tests: 9 cases (happy path, invalid type, magic mismatch, too large, path traversal, missing field, job lookup found/not found/invalid uuid)
- Docker gate documentation

## Out of Scope

- RQ enqueue / worker
- ffmpeg / ffprobe / duration detection
- faster-whisper / transcription
- Screenshots / ZIP packaging
- Download endpoint
- Frontend UI (Jinja2 templates)
- Auth / login / signed sessions / cookies / rate limiting
- Media pipeline architecture / A/B branches
- Skills / MCP

---

## Endpoints

### POST /jobs/upload

**Request:** `multipart/form-data`, field name `file`

**Validation pipeline (in order):**

| Step | Check | Failure |
|---|---|---|
| 1 | Missing `file` field in multipart | 422 |
| 2 | Content-Type not in whitelist | 415 |
| 3 | File extension not in whitelist | 415 |
| 4 | Magic bytes do not match declared type | 400 |
| 5 | Malformed or empty payload | 400 |
| 6 | Streamed cumulative size exceeds `MAX_UPLOAD_BYTES` | 413 |

**MIME type whitelist:** `video/mp4`, `video/webm`, `video/x-matroska`, `video/quicktime`

**Extension whitelist:** `.mp4`, `.webm`, `.mkv`, `.mov`

**Magic byte signatures (MVP minimal):**
- MP4 / MOV: bytes 4–7 == `ftyp`
- WebM / MKV: bytes 0–3 == `\x1a\x45\xdf\xa3`

**Atomicity / cleanup on failure:**
- 422, 415, 400: no file written, no DB row created
- 413: partial file deleted, no DB row created
- DB failure after file save: best-effort file delete + structlog error with `job_id` and `stage`
- On every failure path: no orphan file, no orphan job row

**Storage (success path):**
- File written to `UPLOAD_DIR` as `{uuid4}.{safe_ext}`
- `safe_ext` derived from extension whitelist — never from raw user filename
- User-supplied filename sanitized with `Path(filename).name` for display only; never used in storage path

**Job creation (success path):**
- `jobs` row inserted with `status = pending`
- `session_id`: server-generated UUID4 (see Session ID Design below)
- `video_size_bytes`: actual bytes written to disk
- `original_filename`: `Path(filename).name`
- `stored_filename`: `{uuid4}.{safe_ext}`
- `input_path`: absolute path to stored file (NOT NULL)

**Response 201:**
```json
{
  "job_id": "<uuid>",
  "status": "pending",
  "session_id": "<server-generated-uuid>",
  "original_filename": "lecture.mp4",
  "video_size_bytes": 104857600,
  "created_at": "2026-05-30T12:00:00Z"
}
```

**Error codes:**
- `400` — malformed payload or magic-byte mismatch
- `413` — file exceeds `MAX_UPLOAD_BYTES` (partial file cleaned up)
- `415` — unsupported MIME type or extension
- `422` — missing `file` field in multipart
- `500` — unexpected storage or DB failure

---

### GET /jobs/{job_id}

**Path param:** `job_id` — must be a valid UUID4, else 422

**Response 200:**
```json
{
  "job_id": "<uuid>",
  "status": "pending",
  "original_filename": "lecture.mp4",
  "video_size_bytes": 104857600,
  "created_at": "2026-05-30T12:00:00Z",
  "error_message": null
}
```

**Error codes:**
- `404` — job not found
- `422` — job_id is not a valid UUID

---

## Session ID Design

Server always generates a new anonymous `session_id` (UUID4) per upload request.

- Does not read `X-Session-ID` header
- Does not read cookies
- `session_id` stored in `jobs.session_id` as loose metadata
- `session_id` returned in upload response body
- NOT an auth boundary, NOT an identity boundary, NOT a security boundary
- Auth, signed sessions, cookies, and trusted session lifecycle are deferred to a future milestone

---

## Status Semantics (M002)

`POST /jobs/upload` always creates a job with `status = pending`.

Full lifecycle transitions are deferred:

```
pending → queued → processing → done
                             ↘ failed
```

`queued`, `processing`, `done`, `failed` are set by the worker in a future milestone.

---

## Config

Update existing `app/config.py`:

| Setting | Env var | Default | Behaviour |
|---|---|---|---|
| Upload directory | `UPLOAD_DIR` | `./uploads/` | Create if missing; fail clearly if not writable |
| Max upload bytes | `MAX_UPLOAD_BYTES` | `524288000` (500 MB) | Configurable; tests override to small value (e.g. 1024) |

Update existing `.env.example`:
```
UPLOAD_DIR=./uploads
MAX_UPLOAD_BYTES=524288000
```

`docker-compose.yml`: change only if a runtime volume mount for `UPLOAD_DIR` is strictly required for the Docker gate to pass.

---

## DB Fields Used

No new migration needed — all fields exist in the `jobs` table from Milestone 001.

| Field | Value in M002 |
|---|---|
| `id` | Generated UUID4 |
| `user_id` | `null` (no auth in M002) |
| `session_id` | Server-generated UUID4 |
| `status` | `pending` |
| `original_filename` | `Path(filename).name` |
| `stored_filename` | `{uuid4}.{safe_ext}` |
| `input_path` | Absolute path to stored file (NOT NULL) |
| `video_size_bytes` | Actual bytes written |
| `output_path` | `null` |
| `duration_seconds` | `null` (no ffprobe in M002) |
| `error_message` | `null` on success |
| `created_at` | `now()` |
| `completed_at` | `null` |
| `expires_at` | `null` (cleanup deferred) |

---

## Dependencies

- `python-multipart` — add to `requirements.txt` if not already present (required for FastAPI multipart upload parsing)

---

## Test Matrix

**Size limit testing rule:** Do NOT create real >500 MB files in tests. Override `MAX_UPLOAD_BYTES` to a small value (e.g. 1024 bytes) via test fixture/config.

| Test | Scenario | Expected result |
|---|---|---|
| `test_upload_happy_path` | Valid `.mp4`, within size limit, correct magic bytes | 201, job row created, `status = pending` |
| `test_upload_invalid_type` | `.txt` content-type / extension | 415 |
| `test_upload_magic_mismatch` | `.mp4` extension declared, but text/random content | 400 |
| `test_upload_too_large` | Exceeds `MAX_UPLOAD_BYTES` (overridden to 1 KB) | 413, no file saved, no DB row |
| `test_upload_path_traversal_filename` | Filename `../../evil.mp4` | 201, stored as `{uuid}.mp4`, no path escape |
| `test_upload_missing_file` | No `file` field in multipart | 422 |
| `test_job_lookup_found` | Valid `job_id` of existing job | 200 |
| `test_job_lookup_not_found` | Valid UUID format but unknown job | 404 |
| `test_job_lookup_invalid_uuid` | `not-a-uuid` as path param | 422 |

---

## Implementation Order

1. `app/config.py` — add `UPLOAD_DIR`, `MAX_UPLOAD_BYTES`
2. `app/schemas/job.py` — Pydantic models: `UploadResponse`, `JobStatusResponse`
3. `app/pipeline/upload.py` — streaming save + validation + cleanup
4. `app/api/jobs.py` — router: `POST /jobs/upload`, `GET /jobs/{job_id}`
5. `app/main.py` — include jobs router
6. `requirements.txt` — add `python-multipart` if missing
7. `tests/test_upload.py` — 9 test cases with size limit override
8. `.env.example` — `UPLOAD_DIR`, `MAX_UPLOAD_BYTES` entries

---

## Logging

All log calls must include `job_id` and `stage` per project rules:

```python
logger.info("upload_started", stage="upload", filename=original_filename)
logger.info("upload_saved", job_id=str(job.id), stage="upload", bytes=video_size_bytes)
logger.error("upload_db_failed", job_id=str(job_id), stage="upload", error=str(e))
```

---

## Security Gate (required before merge)

- [ ] No raw user filename in any storage path
- [ ] `stored_filename` is always UUID-based
- [ ] Size limit enforced via streaming (no full read into memory)
- [ ] Partial file deleted on 413
- [ ] Triple validation: Content-Type + extension + magic bytes
- [ ] No ffmpeg, no subprocess calls
- [ ] `session_id` is server-generated, not from client header
- [ ] No hardcoded secrets

---

## Acceptance Criteria

- [ ] `POST /jobs/upload` → 201 with `job_id` on valid upload
- [ ] File saved as `{uuid4}.{ext}` in `UPLOAD_DIR` (never original filename)
- [ ] `jobs` row created with `status = pending`
- [ ] `session_id` in response is server-generated
- [ ] `GET /jobs/{job_id}` → 200 with job metadata
- [ ] Unsupported type/extension → 415
- [ ] Magic-byte mismatch → 400
- [ ] Exceeds size limit → 413, partial file cleaned up, no DB row
- [ ] Missing multipart field → 422
- [ ] Path traversal filename handled safely
- [ ] No RQ enqueue, no worker, no ffmpeg
- [ ] All 9 test cases pass
- [ ] Docker gate passes

---

## Docker Gate

```bash
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app
# POST /jobs/upload with a test video file → 201, job_id returned
# GET /jobs/{job_id} → 200, status=pending
# GET /health → {"status":"ok","db":"ok","redis":"ok"}
pytest tests/ -v
```
