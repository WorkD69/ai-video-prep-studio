# Milestone 007 - 24h File Retention + Cleanup

## Summary

Close the 24-hour file retention loop and eliminate unbounded growth of the `uploads/` directory.

After M007:
- The uploaded input file is deleted from `uploads/` immediately after the worker finishes
  (whether success or failure).
- A background scheduler runs every `CLEANUP_INTERVAL_SECONDS` (default 10 min) and deletes:
  - Output ZIPs for jobs whose `expires_at < now`.
  - Any surviving input files for expired jobs (safety net if immediate cleanup failed).
- A user who visits `/download/{job_id}` after the ZIP is deleted receives **410 Gone**
  (this already works — M007 completes the physical deletion side of that contract).

The `expires_at` DB column, the 410 response in `download.py`, and the scheduled-cleanup
architecture note ("24h cleanup cron") were all designed in M001/M005. M007 is the
implementation.

## Branch naming

- **Spec (docs-only, current):** `docs/milestone-007-file-retention-cleanup`
- **Implementation:** `feature/milestone-007-file-retention-cleanup`

## Responsible agents

- Implementation: `backend-agent`
- Test writing: `qa-agent`
- Security review: `security-agent` (required before merge — see Quality Gates)

---

## In Scope

1. **Worker: immediate input-file cleanup**
   - Delete `job.input_path` in `process_job.py`'s outer `finally` block (success + failure).
   - Log deletion outcome at INFO level. If the file is already gone, log and continue — do not raise.

2. **`app/services/cleanup.py`** — new module
   - Pure cleanup function `cleanup_expired_jobs(db: Session, *, output_dir: Path, upload_dir: Path) -> CleanupResult` (testable without scheduler).
   - `CleanupResult`: named tuple / dataclass with `deleted_zips: int`, `deleted_inputs: int`, `errors: int`.
   - Targets jobs where `expires_at < datetime.utcnow()`.
   - For each expired job: try to delete `output_path` file; try to delete `input_path` file.
   - Validates each path against its respective configured directory before deleting
     (defense in depth — same pattern as `download.py`).
   - One job failure must not block others: catch exceptions per job, increment `errors`, log, continue.
   - Does NOT delete DB records (rows kept for audit purposes).
   - Does NOT change job `status` or any DB field.

3. **FastAPI lifespan + asyncio background task** — `app/main.py`
   - Convert `app` creation to use `@asynccontextmanager` lifespan.
   - On startup: launch `asyncio.create_task(_cleanup_loop())`.
   - `_cleanup_loop()`: `asyncio.sleep(settings.cleanup_interval_seconds)` first,
     then call `cleanup_expired_jobs` in a thread executor (it's a sync DB function),
     then repeat indefinitely. `CancelledError` exits cleanly.
   - On shutdown: cancel the task and await it with `return_exceptions=True`.

4. **`app/config.py`** — new setting
   - `cleanup_interval_seconds: int = 600` (10 minutes default, configurable via env).

5. **`tests/test_cleanup.py`** — new test file
   - See Testing Requirements section.

6. **`AI_WORKLOG.md`** — milestone entry.

## Out of Scope

- Deleting DB records (job rows are retained indefinitely for audit).
- Admin endpoint to trigger manual cleanup.
- S3 / cloud storage cleanup.
- Email/webhook notification on cleanup.
- Retry or alerting for cleanup failures (log only in MVP).
- Cleanup of `outputs/staging/` — that's already handled by `mock_pipeline.py`'s `finally` block.
- Any change to `download.py` — the 410 logic is already correct.
- Alembic migration — `expires_at` column exists since M001.
- APScheduler or rq-scheduler — zero new dependencies; asyncio background task is sufficient.

---

## Current state after M006

- `jobs.expires_at` set to `created_at + 24h` at upload time (M001/M002).
- `GET /download/{job_id}` returns 410 when `expires_at < now` (M005), checked **before**
  file-existence check — so the 410 path works correctly even after the file is deleted.
- `process_job.py` does NOT delete `input_path` — uploads accumulate on disk.
- `mock_pipeline.py` `finally` already cleans `staging_dir` — no change needed there.
- No scheduler or lifespan hook exists in `app/main.py`.
- 154 tests pass (all M001–M006 green).

---

## Cleanup function contract

```python
# app/services/cleanup.py

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session

@dataclass
class CleanupResult:
    deleted_zips: int
    deleted_inputs: int
    errors: int

def cleanup_expired_jobs(
    db: Session,
    *,
    output_dir: Path,
    upload_dir: Path,
) -> CleanupResult:
    """Delete files for expired jobs. Does not modify DB records."""
    ...
```

**Query:**
```sql
SELECT * FROM jobs WHERE expires_at < NOW()
```

**Per-job logic (pseudocode):**
```
for job in expired_jobs:
    try:
        if job.output_path:
            path = Path(job.output_path).resolve()
            if not path.is_relative_to(output_dir.resolve()):
                log CRITICAL path_traversal_detected; errors += 1
            elif path.exists():
                path.unlink()
                deleted_zips += 1
            # else: file already gone — no counter increment, no error

        if job.input_path:
            path = Path(job.input_path).resolve()
            if not path.is_relative_to(upload_dir.resolve()):
                log CRITICAL path_traversal_detected; errors += 1
            elif path.exists():
                path.unlink()
                deleted_inputs += 1
            # else: file already gone — no counter increment, no error

    except Exception as e:
        log ERROR cleanup_job_error; errors += 1
        continue  # never block other jobs
```

**Counter semantics (explicit):**
- `deleted_zips` / `deleted_inputs`: incremented only when the file **existed and was successfully deleted**.
- A file that is already missing: silently skipped — no counter increment, no error.
- A path outside the allowed directory: `errors += 1`, file not touched.
- An unexpected exception during a job's processing: `errors += 1`, remaining jobs continue.

**Shared helper — `safe_delete`:**

Extract path-validation + deletion into a reusable helper so both the scheduled cleanup and the
worker's immediate cleanup use identical logic. Both callers live in different modules but import
from `app/services/cleanup.py`.

```python
def safe_delete(path_str: str, allowed_dir: Path, log_context: dict) -> str:
    """Validate path against allowed_dir and delete if it exists.

    Returns one of: "deleted" | "missing" | "traversal" | "error"
    Callers decide how to increment counters based on the return value.
    Never raises.
    """
    ...
```

Return values and caller behavior:
- `"deleted"` → increment the relevant counter
- `"missing"` → no counter change, no error
- `"traversal"` → log CRITICAL, increment `errors`
- `"error"` → log ERROR, increment `errors`

**Logging:**
```python
logger.info("cleanup_run", deleted_zips=N, deleted_inputs=N, errors=N, stage="cleanup")
```

---

## Worker change

In `app/workers/process_job.py`, the outer `finally` block becomes:

```python
finally:
    # Delete input file after processing (success or failure).
    # Uses safe_delete from app/services/cleanup.py for consistent path validation.
    # Safety net: cleanup_expired_jobs handles any survivors at 24h.
    if job is not None and job.input_path:
        outcome = safe_delete(
            job.input_path,
            allowed_dir=Path(settings.upload_dir).resolve(),
            log_context={"job_id": job_id, "stage": "worker"},
        )
        if outcome == "deleted":
            logger.info("worker_input_cleaned", job_id=job_id, stage="worker")
        elif outcome == "missing":
            logger.info("worker_input_already_gone", job_id=job_id, stage="worker")
        # "traversal" and "error" are logged by safe_delete itself
    db.close()
```

Note: Although `job.input_path` is a server-constructed path (UUID-based filename, never derived
from user input), `safe_delete` still validates it against `upload_dir` as defense-in-depth —
consistent with how `cleanup_expired_jobs` and `download.py` handle DB-backed paths.

---

## Lifespan wiring (`app/main.py`)

```python
from contextlib import asynccontextmanager
import asyncio
from app.services.cleanup import cleanup_expired_jobs
from app.database import SessionLocal
from app.config import settings
from pathlib import Path

async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _run_cleanup_sync)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("cleanup_loop_unexpected_error", stage="cleanup")

def _run_cleanup_sync() -> None:
    db = SessionLocal()
    try:
        result = cleanup_expired_jobs(
            db,
            output_dir=Path(settings.output_dir).resolve(),
            upload_dir=Path(settings.upload_dir).resolve(),
        )
        logger.info(
            "cleanup_run",
            deleted_zips=result.deleted_zips,
            deleted_inputs=result.deleted_inputs,
            errors=result.errors,
            stage="cleanup",
        )
    except Exception:
        logger.exception("cleanup_sync_error", stage="cleanup")
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app_: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

app = FastAPI(title="AI Video Prep Studio", version="0.1.0", lifespan=lifespan)
```

Implementation may adapt these shapes; the contract is: loop starts on startup, exits cleanly
on shutdown, one exception in a run does not kill the loop.

---

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `cleanup_interval_seconds` | `600` | Seconds between cleanup runs. Configurable via `CLEANUP_INTERVAL_SECONDS` env var. |

No secrets. No external API keys.

---

## Files expected to change during implementation

```
app/services/__init__.py          # New: package init
app/services/cleanup.py           # New: cleanup_expired_jobs, safe_delete, CleanupResult
app/workers/process_job.py        # Modified: call safe_delete for input_path in finally
app/config.py                     # Modified: add cleanup_interval_seconds
app/main.py                       # Modified: add lifespan, background task
tests/test_cleanup.py             # New: cleanup unit tests
AI_WORKLOG.md                     # Milestone entry
```

No changes to:
- `app/api/download.py` (410 logic already correct)
- `app/pipeline/` (mock_pipeline staging cleanup unchanged)
- `app/models/` (no schema change)
- `alembic/` (no migration)
- `app/templates/` (no UI change)

---

## Testing requirements

All tests deterministic, fast (< 60s total), no real video, no network.

**File:** `tests/test_cleanup.py`

Use the existing `db_session` fixture from `conftest.py`. Mock the filesystem using `tmp_path`
(pytest built-in).

| Test | Setup | Expected |
|------|-------|----------|
| `test_cleanup_deletes_expired_zip` | Job with `expires_at` in past, `output_path` points to a real file in `tmp_path/outputs/` | File deleted; `result.deleted_zips == 1` |
| `test_cleanup_deletes_expired_input` | Job with `expires_at` in past, `input_path` points to a real file in `tmp_path/uploads/` | File deleted; `result.deleted_inputs == 1` |
| `test_cleanup_skips_non_expired` | Job with `expires_at` in future | No files deleted; `result == CleanupResult(0, 0, 0)` |
| `test_cleanup_missing_file_no_error` | Job with `expires_at` in past, `output_path` file does not exist | No exception raised; `result.errors == 0` |
| `test_cleanup_multiple_expired_jobs` | 3 expired jobs, each with output_path file | All 3 files deleted; `result.deleted_zips == 3` |
| `test_cleanup_one_error_continues` | 2 expired jobs; patch `Path.unlink` to raise on first call | Second job still processed; `result.errors == 1`, `result.deleted_zips == 1` |
| `test_cleanup_path_traversal_rejected` | Job with `output_path` set to path outside `output_dir` (e.g. `/etc/passwd`) | File not deleted; `result.errors == 1` |
| `test_cleanup_input_path_traversal_rejected` | Job with `input_path` set to path outside `upload_dir` (e.g. `/etc/shadow`) | File not deleted; `result.errors == 1` |
| `test_worker_deletes_input_on_success` | Upload file exists on disk | After `process_job()`, input file is gone |
| `test_worker_deletes_input_on_failure` | Upload file exists on disk, worker set to fail | After `process_job()` raises, input file is gone |
| `test_worker_rejects_input_path_outside_upload_dir` | Job with `input_path` outside `upload_dir`; patch `safe_delete` to capture args | `safe_delete` called with correct `allowed_dir`; file at the out-of-bounds path not deleted |

Total: 11 test cases.

**Testing the lifespan/scheduler:**
Do NOT test the scheduler loop directly — it's async infrastructure. Test `cleanup_expired_jobs`
as a pure function. The scheduler integration is covered by the Docker manual gate.

---

## Security requirements

### Path validation

Before calling `unlink()` on any path from DB, verify it is inside the configured directory:
```python
resolved = Path(job.output_path).resolve()
if not resolved.is_relative_to(output_dir):
    logger.critical("cleanup_path_traversal", ...)
    errors += 1
    continue  # do not delete
```

This mirrors the existing pattern in `download.py`.

### No shell commands

Cleanup uses only Python `pathlib.Path.unlink()`. No `subprocess`, no `shell=True`.

### DB records not deleted

Cleanup only removes files from disk. DB records are never deleted. No risk of data loss.

### Input path trust

`job.input_path` is a server-constructed path (UUID-based filename, set at upload time in
`jobs.py`). It is still validated against `upload_dir` before deletion as defense-in-depth.

---

## Docker / manual gate

```powershell
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app worker

# Health check
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json
# Expected: status=ok, db=ok, redis=ok

# Baseline: upload a file, confirm it processes
# ... upload small MP4 via browser, wait for done status ...

# Verify input file cleanup after processing:
# (After job is done, the uploaded file in uploads/ should be gone)
Get-ChildItem ./uploads/   # Expected: empty or no new files

# Verify scheduled cleanup at 24h:
# Option A (quick smoke): manually set expires_at < now in DB and wait one cleanup interval
# psql: UPDATE jobs SET expires_at = NOW() - INTERVAL '1 minute' WHERE id = '<job_id>';
# After CLEANUP_INTERVAL_SECONDS, check that output ZIP is deleted:
Test-Path "./outputs/<zip_filename>"   # Expected: False

# Confirm 410 after cleanup:
try {
  Invoke-WebRequest "http://localhost:8000/download/<job_id>" -ErrorAction Stop
} catch {
  [int]$_.Exception.Response.StatusCode   # Expected: 410
}
```

---

## Acceptance criteria

**AC1** — After `process_job()` completes (success or failure), the uploaded input file at
`job.input_path` no longer exists on disk.

**AC2** — `cleanup_expired_jobs()` deletes the output ZIP for every job where
`expires_at < datetime.utcnow()` and `output_path` is non-null and the file exists.

**AC3** — `cleanup_expired_jobs()` deletes the input file for every job where
`expires_at < datetime.utcnow()` and `input_path` is non-null and the file exists
(safety-net for cases where immediate cleanup in the worker failed).

**AC4** — `cleanup_expired_jobs()` skips jobs whose `expires_at >= datetime.utcnow()`.
No non-expired files are deleted.

**AC5** — A per-job exception in `cleanup_expired_jobs()` does not prevent other jobs
from being processed in the same run.

**AC6** — Paths from DB are validated against `output_dir` / `upload_dir` before deletion.
Any path outside the expected directory is logged as CRITICAL and skipped.

**AC7** — `GET /download/{job_id}` returns **410 Gone** after the cleanup has deleted the
ZIP for that job (the expiry check in `download.py` fires correctly since the file's expiry
precedes cleanup).

**AC8** — No Alembic migration introduced.

**AC9** — `tests/test_cleanup.py` contains all 11 required test cases and passes.

**AC10** — All existing M001–M006 tests remain green. `pytest tests/ -v` completes under 60s.

**AC11** — `cleanup_interval_seconds` is configurable via environment variable
`CLEANUP_INTERVAL_SECONDS`.

**AC12** — The FastAPI app starts and the health endpoint responds after M007 changes to
`main.py` (lifespan conversion must not break startup).

**AC13** — Security review by `security-agent` completed in clean context before merge;
no High or Critical open findings remain.

**AC14** — Docker manual gate executed and recorded in `AI_WORKLOG.md`.

---

## Quality gates

| Gate | Required | Notes |
|------|----------|-------|
| Functional (manual test) | Always | Upload → processing → verify input gone; set expiry in past → verify ZIP deleted + 410 |
| Tests (pytest green) | Always | All tests including M001–M006 regression |
| Security review | Always | `security-agent`, clean context, diff + ACs. Path traversal check is critical. |
| Human understanding | Always | Reviewer can explain: why expiry check precedes file-existence check in download.py |
| DB migration | N/A | No migration needed |
| Performance | N/A | Cleanup is low-frequency background task; no hot-path impact |
| Documentation | N/A | No interface contract change; no ADR needed |

---

## Implementation order

1. Add `app/services/__init__.py` and `app/services/cleanup.py` with `safe_delete`, `CleanupResult`, and `cleanup_expired_jobs`.
2. Write unit tests in `tests/test_cleanup.py` for the cleanup function.
3. Run `pytest tests/test_cleanup.py -v` — must be green.
4. Modify `app/workers/process_job.py` — call `safe_delete` for `input_path` in `finally` block.
5. Write `test_worker_deletes_input_on_success`, `test_worker_deletes_input_on_failure`, and `test_worker_rejects_input_path_outside_upload_dir`.
6. Run `pytest tests/ -v` — must be green (all existing + new).
7. Add `cleanup_interval_seconds` to `app/config.py`.
8. Convert `app/main.py` to lifespan; wire `_cleanup_loop`.
9. Run `pytest tests/ -v` — confirm lifespan change doesn't break existing tests.
10. Run Docker canonical gate + manual smoke (upload, verify input cleaned, set expiry, verify ZIP deleted + 410).
11. Update `AI_WORKLOG.md`.
12. Activate `security-agent` in clean context (diff + ACs).
13. Activate Codex Reviewer in clean context (diff + ACs).
14. Merge after all gates green.

---

## Open questions / deferred risks

**1. Clock skew in tests.**
`cleanup_expired_jobs` uses `datetime.utcnow()` internally. Tests must set `expires_at` to
`datetime.utcnow() - timedelta(seconds=1)` (in the past) or `+ timedelta(hours=24)` (in the
future). Do not freeze time with `freezegun` unless needed — just set `expires_at` directly
when creating the test Job record.

**2. Lifespan vs startup events.**
`app/main.py` currently uses no startup events. Converting to lifespan is backward-compatible
and is the recommended FastAPI pattern (startup events are deprecated). The conversion should
be straightforward — no existing event handlers to migrate.

**3. Input-file cleanup race condition.**
If the worker crashes between writing `done` to DB and deleting the input file, the file
survives until the 24h cleanup runs. This is acceptable for MVP: the 24h safety net is
exactly the scheduled cleanup in AC3.

**4. Next milestone: 1 active job per session/IP (M008).**
The current `session_id` is a fresh `uuid4()` per upload and is not tied to any browser cookie.
Implementing the 1-job limit requires a decision on the session mechanism (cookie-based,
IP-based, or signed token). This decision should go to ADR 004 before M008 begins.

**5. Output staging dir.**
`mock_pipeline.py` already cleans `outputs/staging/{job_id}/` via `shutil.rmtree` in `finally`.
M007 does not need to clean staging dirs — they are transient and already handled.

**6. Cleanup on `pending` / `queued` jobs.**
If a job is stuck in `pending` or `queued` past its `expires_at` (e.g., worker never picked it
up), M007 cleanup will delete its input file. The job status remains `pending`/`queued` in DB
(no file to serve, but no output_path either). A future milestone could mark these as `failed`.
For MVP, the delete-files behavior is correct; stale DB rows are acceptable.
