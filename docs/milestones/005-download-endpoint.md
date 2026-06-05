# Milestone 005 - Download Endpoint

## Summary

Implement `GET /download/{job_id}` - the final MVP endpoint that allows users to download
the ZIP artifact produced by the pipeline. After M005, the full end-to-end flow is complete:
upload -> process -> download.

M004 already produces the ZIP and sets `jobs.output_path`. M005 exposes that artifact through
a secure, path-traversal-safe HTTP endpoint with correct error semantics for every observable
job state.

## Branch naming

- **Spec (docs-only, current):** `docs/milestone-005-download-endpoint-spec`
- **Implementation:** `feature/milestone-005-download-endpoint`

## Responsible agent

Implementation: `backend-agent` (see [docs/agents/backend-agent.md](../agents/backend-agent.md))
Security review: `security-agent` (see [docs/agents/security-agent.md](../agents/security-agent.md))
Security review is **required** before merge (see Quality Gates).

---

## In Scope

- Root-mounted `GET /download/{job_id}` route
- All error conditions with correct HTTP status codes
- Path traversal protection on `output_path`
- Safe `Content-Disposition` header
- `FileResponse` serving
- structlog logging for every outcome
- Tests in `tests/test_download.py`
- Security review by `security-agent` in clean context

## Out of Scope

- Frontend status page with download button / HTMX changes
- 24h cleanup reaper (deferred)
- Rate limiting on the download endpoint
- Byte-range partial download (206 Partial Content)
- Per-user auth / download restrictions
- S3 / cloud storage
- Real ffmpeg or faster-whisper
- Retry logic
- Email notification on completion
- New Alembic migration (no schema change needed)

---

## Current state after M004

- `jobs.output_path` is set to the absolute path of the produced ZIP on success.
- `jobs.expires_at` is set to `created_at + 24h` at upload time.
- `jobs.status` is `done` when the ZIP is ready.
- `output_dir` is available via `settings.output_dir` (default `./outputs`).
- The `GET /download/{job_id}` route does not yet exist.

---

## Endpoint contract

### `GET /download/{job_id}`

**Path parameter:** `job_id` - validated as UUID by FastAPI. Non-UUID values return 422
automatically (FastAPI default behavior; no extra code needed).

**Response on success (200):**

```
HTTP/1.1 200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename="llm_analysis_package_lecture_20260604_123456.zip"
```

Body: ZIP file bytes (streamed via `FileResponse`).

---

### Error conditions and check order

Checks are executed in this strict order. Each check short-circuits on failure.

| Step | Condition | HTTP Status | `detail` string |
|------|-----------|-------------|-----------------|
| 1 | `job_id` is not a valid UUID | **422** Unprocessable Entity | FastAPI default |
| 2 | `db.get(Job, job_id)` returns `None` | **404** Not Found | `"job_not_found"` |
| 3 | `job.status != JobStatus.done` | **409** Conflict | `"job_not_ready"` |
| 4 | `datetime.utcnow() > job.expires_at` | **410** Gone | `"job_expired"` |
| 5 | `job.output_path is None` | **500** Internal Server Error | `"output_path_missing"` |
| 6 | `not Path(job.output_path).resolve().is_relative_to(resolved_output_dir)` | **500** Internal Server Error | `"internal_error"` |
| 7 | `not resolved_path.exists()` | **500** Internal Server Error | `"output_file_missing"` |
| 8 | All checks pass | **200** OK | - |

**Rationale for check order:**

- Step 2 before Step 3: return 404 (not 409) for unknown jobs - don't leak status of non-existent jobs.
- Step 3 before Step 4: a job that is `processing` should return 409 even if it has somehow
  exceeded `expires_at` (edge case; processing failure is the more informative signal).
- Step 5 before Step 6: cannot resolve a NULL path; treat NULL as an invariant violation.
- **Step 6 before Step 7:** resolve and validate the path is within `output_dir` *before*
  checking whether the file exists. The endpoint must never probe the filesystem outside
  `output_dir`, even if `output_path` in the DB is somehow corrupted.
- Step 7 last: only check file existence after all guards have passed.

**Error response body:** `{"detail": "<short_safe_string>"}` - no raw filesystem paths, no
tracebacks, no host-internal information.

---

## Security requirements

### Path traversal protection

`job_id` from the URL path is used **only** for the DB lookup, never for filesystem path
construction. The `output_path` comes exclusively from `jobs.output_path` DB column.

Before serving the file, the implementation **must**:

```python
output_dir = Path(settings.output_dir).resolve()
resolved = Path(job.output_path).resolve()

if not resolved.is_relative_to(output_dir):
    logger.critical(
        "download_path_traversal_detected",
        job_id=str(job.id),
        stage="download",
    )
    raise HTTPException(status_code=500, detail="internal_error")
```

This check runs **before** `resolved.exists()` to avoid probing paths outside `output_dir`.

### Content-Disposition safety

- Use `Path(job.output_path).name` - basename only, no directory components.
- The name is already M004-sanitized (`safe_stem` from `make_safe_stem()` in
  `app/pipeline/zip_packaging.py`): only `[a-zA-Z0-9_-]` + timestamp suffix.
- **Never** use `job.original_filename` in `Content-Disposition`.
- **Never** derive the filename from any part of the request URL.

The `filename` passed to `FileResponse` must be:
```python
filename = Path(job.output_path).name  # e.g. "llm_analysis_package_lecture_20260604_123456.zip"
```

Starlette's `FileResponse` sets:
```
Content-Disposition: attachment; filename="llm_analysis_package_lecture_20260604_123456.zip"
```

Non-ASCII characters cannot appear in `safe_stem` (replaced by `_` in M004), so RFC 6266
`filename*` encoding is not required for MVP.

---

## FileResponse vs StreamingResponse decision

**Decision: `FileResponse`**

| Criterion | FileResponse | StreamingResponse |
|-----------|-------------|-------------------|
| Range requests (resume) | Handled natively by Starlette | Manual implementation required |
| ETag / Last-Modified | Provided automatically | Manual |
| Memory usage | Streams via OS (no full read) | Streams via generator |
| Content-Disposition | Set via `filename=` kwarg | Manual header |
| Complexity | Minimal | Higher |

`FileResponse` does not read the entire ZIP into memory. Starlette uses `aiofiles` or
sendfile to stream the file in chunks. For MVP file sizes (1-200 MB), this is fully
adequate.

**Usage pattern:**

```python
return FileResponse(
    path=str(resolved),
    media_type="application/zip",
    filename=Path(job.output_path).name,
)
```

---

## Logging requirements

Every request outcome must be logged with `job_id` and `stage="download"` using structlog.
Bind `job_id` at the start of the request handler.

| Outcome | Log event | Level | Extra fields |
|---------|-----------|-------|--------------|
| 200 OK | `download_served` | info | `filename`, `file_size_bytes` |
| 404 | `download_job_not_found` | info | - |
| 409 | `download_job_not_ready` | info | `status` |
| 410 | `download_expired` | info | `expires_at` |
| 500 output_path NULL | `download_output_path_null` | error | - |
| 500 path traversal | `download_path_traversal_detected` | critical | - |
| 500 file missing | `download_file_missing` | error | - |

**Do not log** raw filesystem paths in any response body or client-visible field.
Server-side logs may include `output_path` for the `download_file_missing` case (ops debugging).

---

## Implementation notes

### Route location

The route must be mounted at exactly `/download/{job_id}`.

Preferred implementation:
- Add `app/api/download.py` with an unprefixed `APIRouter(tags=["download"])`.
- Include it from `app/main.py`.

If keeping the handler near `app/api/jobs.py`, do not add it to the existing `router`
because that router has `prefix="/jobs"` and would create `/jobs/download/{job_id}`.
Use a separate unprefixed router and include it from `app/main.py`.

### Dependency injection

Use the existing `get_db` dependency for the database session. Do not open a raw session.

### Settings access

Access settings via `from app.config import settings` (module-level singleton).
This is the existing pattern used throughout the codebase. Do not create a
`get_settings` dependency function - it does not exist in this project.

### No DB migration

No new columns, no new tables, no Alembic revision. Uses existing `jobs.output_path` and
`jobs.expires_at`.

---

## Files expected to change during implementation

```
app/api/download.py          # add root-mounted GET /download/{job_id} route
app/main.py                  # include the unprefixed download router
tests/test_download.py       # new test file (see below)
AI_WORKLOG.md                # milestone entry
```

No changes to:
- `app/pipeline/` (M004 contracts are stable)
- `app/models/` (no schema change)
- `app/workers/` (worker is not involved)
- `alembic/` (no migration)
- `app/templates/` (no frontend changes)

---

## Testing requirements

All tests must be deterministic, fast (< 60s total), and require no pipeline-generated ZIP,
no faster-whisper, and no ffmpeg. Create a tiny temporary ZIP file on disk with deterministic
bytes for `FileResponse` happy-path tests (e.g. `zipfile.ZipFile` with a single dummy entry).
Corruption/invariant cases (path traversal, null `output_path`, missing file on disk) do not
need a real file on disk - mock the DB row and filesystem state as needed.

**File:** `tests/test_download.py`

Required test cases (12 total):

| Test | Setup | Expected |
|------|-------|----------|
| `test_download_happy_path` | done job, valid ZIP tempfile, expires_at future | 200, `Content-Disposition` contains basename, body is ZIP bytes |
| `test_download_job_not_found` | unknown UUID | 404, `detail="job_not_found"` |
| `test_download_job_not_done_pending` | job status=pending | 409, `detail="job_not_ready"` |
| `test_download_job_not_done_queued` | job status=queued | 409, `detail="job_not_ready"` |
| `test_download_job_not_done_processing` | job status=processing | 409, `detail="job_not_ready"` |
| `test_download_job_not_done_failed` | job status=failed | 409, `detail="job_not_ready"` |
| `test_download_expired` | done job, expires_at in past | 410, `detail="job_expired"` |
| `test_download_output_path_null` | done job, `output_path=None` | 500, `detail="output_path_missing"` |
| `test_download_path_traversal_db` | done job, `output_path` outside `output_dir` | 500, `detail="internal_error"` |
| `test_download_file_missing` | done job, path inside `output_dir`, file not on disk | 500, `detail="output_file_missing"` |
| `test_download_invalid_job_id` | non-UUID string in path | 422 |
| `test_download_content_disposition_safe` | done job with sanitized filename | `Content-Disposition` filename = basename of `output_path`; does not contain `original_filename` |

**Additional invariant to verify in test:**
`test_download_path_traversal_db` must confirm that a path pointing to a real file
*outside* `output_dir` is rejected with 500 - i.e., the traversal check fires before
any file existence check.

---

## Security gate

`security-agent` must review the implementation in a clean context before merge.

Provide: diff + this acceptance criteria section.

Focus areas for the review:
- Path traversal: `resolved.is_relative_to(output_dir)` fires before `resolved.exists()`
- Content-Disposition: no `original_filename` or user-controlled value in filename
- No request URL component used for FS path construction
- `job_id` used only as DB key, not as a path segment
- No raw paths, tracebacks, or host info in error responses
- Structlog `critical` fires on traversal detection

---

## Docker / manual gate

```powershell
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app worker

# Confirm health
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json

# Happy path smoke (after uploading a video and waiting for done):
# Replace {JOB_ID} with actual job UUID from upload response
Invoke-WebRequest http://localhost:8000/download/{JOB_ID} -OutFile test_download.zip
# Verify: test_download.zip is a valid ZIP and opens correctly

# 404 smoke:
try {
  Invoke-WebRequest `
    http://localhost:8000/download/00000000-0000-0000-0000-000000000000 `
    -ErrorAction Stop
} catch {
  [int]$_.Exception.Response.StatusCode
}
# Expected: 404

# 409 smoke (deterministic - stop worker so job stays queued):
docker compose stop worker
# Upload a video; job will remain in status=queued with no worker running.
# Replace {JOB_ID} with the UUID returned by the upload response.
try {
  Invoke-WebRequest http://localhost:8000/download/{JOB_ID} -ErrorAction Stop
} catch {
  [int]$_.Exception.Response.StatusCode
}
# Expected: 409
# Restart worker when done:
docker compose start worker

# 422 smoke (invalid UUID):
try {
  Invoke-WebRequest http://localhost:8000/download/not-a-uuid -ErrorAction Stop
} catch {
  [int]$_.Exception.Response.StatusCode
}
# Expected: 422

# Note: corruption/invariant cases (path traversal, null output_path, missing file on disk)
# are covered deterministically by pytest, not by manual smoke.
```

---

## Acceptance criteria

**AC1** - `GET /download/{job_id}` returns 200 and a valid ZIP response for a job with
`status=done`, non-expired `expires_at`, and an existing `output_path` file.

**AC2** - The route is mounted at exactly `/download/{job_id}`, not
`/jobs/download/{job_id}`.

**AC3** - Returns 404 with `detail="job_not_found"` if `job_id` is a valid UUID but
no matching job exists in the DB.

**AC4** - Returns 409 with `detail="job_not_ready"` for any non-done status:
`pending`, `queued`, `processing`, `failed`.

**AC5** - Returns 410 with `detail="job_expired"` if `job.expires_at < datetime.utcnow()`,
even if `status=done` and the file exists on disk.

**AC6** - Returns 500 with `detail="output_path_missing"` if `job.status=done` but
`job.output_path is None`.

**AC7** - Returns 500 with `detail="internal_error"` if `Path(job.output_path).resolve()`
is not relative to `Path(settings.output_dir).resolve()`. This check runs **before**
checking whether the file exists on disk.

**AC8** - Returns 500 with `detail="output_file_missing"` if `job.output_path` passes
the traversal guard but the file does not exist on disk.

**AC9** - `output_path` is sourced exclusively from `jobs.output_path`; no part of the
request URL (beyond `job_id` for the DB lookup) contributes to the filesystem path.

**AC10** - `Content-Disposition: attachment; filename="<name>"` where `<name>` is
`Path(job.output_path).name` (basename only). `job.original_filename` does not appear
anywhere in the response headers.

**AC11** - Response uses `FileResponse`, not a manual `StreamingResponse`.

**AC12** - No DB migration introduced.

**AC13** - No frontend / Jinja2 template changes.

**AC14** - `tests/test_download.py` contains all 12 required test cases and passes;
`pytest tests/ -v` completes under 60 seconds.

**AC15** - Security review by `security-agent` completed in clean context before merge;
no High or Critical open findings remain.

**AC16** - All existing M001-M004 tests remain green.

---

## Quality gates

| Gate | Required | Notes |
|------|----------|-------|
| Functional (manual test) | Always | curl/Invoke-WebRequest against running stack |
| Tests (pytest green) | Always | All tests including regression |
| Security review | Always | `security-agent`, clean context, diff + ACs |
| Human understanding | Always | Reviewer can explain the path validation logic |
| DB migration | N/A | No migration needed |
| Performance | Contextual | Not required for MVP - `FileResponse` is adequate |
| Documentation | Contextual | No interface contract change; no ADR needed |

---

## Open questions / deferred risks

**1. 410 vs 404 information leakage after expiry.**
Returning 410 (vs 404) reveals that a job *existed* but has expired. In MVP there is no
auth boundary, so this is acceptable. Revisit when user auth is added (future milestone).

**2. Cleanup race condition.**
A file could be deleted by the cleanup reaper between the 410 check (step 4) and the
`FileResponse` call (step 8). Mitigated by returning 500 on missing file (step 7).
The check order (traversal guard -> exists -> serve) keeps the window minimal.
True fix requires serving from an atomic copy or locking mechanism - deferred.

**3. `output_path = None` on a `done` job.**
Should never happen if M004's guarded `WHERE status='processing'` write is correct.
Treated as 500 invariant violation. Consider adding a monitoring alert or a DB constraint
(`CHECK (status != 'done' OR output_path IS NOT NULL)`) in a future milestone.

**4. Download filename encoding.**
Non-ASCII characters in `safe_stem` are already replaced by `_` in M004's `make_safe_stem()`.
RFC 6266 `filename*` UTF-8 encoding is therefore not required for MVP. If non-ASCII filenames
become a requirement, add a dedicated ADR.

**5. Concurrent download requests.**
Single worker, single user per session in MVP - no concurrency concern. Multiple simultaneous
download requests for the same job are safe (read-only FS access, no shared mutable state).

---

## Implementation order

1. Add `GET /download/{job_id}` route with all error checks and `FileResponse`.
2. Add structlog calls for each outcome.
3. Write `tests/test_download.py` with all 12 test cases.
4. Run `pytest tests/ -v` - must be green.
5. Run Docker canonical gate + manual smoke (happy path + 404 + 409).
6. Update `AI_WORKLOG.md`.
7. Activate `security-agent` in clean context (diff + ACs).
8. Activate `Codex Reviewer` in clean context (diff + ACs).
9. Merge after all gates green and security review complete.
