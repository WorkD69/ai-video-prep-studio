# Milestone 006 - Job Status Page with HTMX Polling

## Summary

Add the browser-facing frontend layer: an upload form and a job status page with HTMX polling.
After M006, a non-developer can open the app in a browser, upload a video, watch the job
progress, and click a download button — no API client needed.

M001–M005 built the full API backend (upload, job lifecycle, download). M006 connects those
endpoints to a Jinja2 + HTMX + TailwindCSS UI. The existing JSON API is not broken or replaced;
the HTML routes are added alongside it.

**Core user journey unlocked by M006:**

```
GET /  →  Upload form  →  POST /jobs/upload  →  GET /status/{job_id}  →  HTMX polling  →  Download button
```

## Branch naming

- **Spec (docs-only, current):** `docs/milestone-006-job-status-page`
- **Implementation:** `feature/milestone-006-job-status-page`

## Responsible agents

- Implementation: `backend-agent` (Jinja2 routes, template data) + responsible for templates
- Test writing: `qa-agent` (see [docs/agents/qa-agent.md](../agents/qa-agent.md))
- Security review: `security-agent` (see [docs/agents/security-agent.md](../agents/security-agent.md))
- Security review is **required** before merge (see Quality Gates)

---

## In Scope

- Jinja2Templates setup in FastAPI (`app/api/pages.py` + wired into `app/main.py`)
- `GET /` — Upload form page (Jinja2 template, HTMX multi-part POST)
- `POST /jobs/upload` — minor modification: add `HX-Redirect` response header when
  `HX-Request` header is present in the request (HTMX upload redirect to status page)
- `GET /status/{job_id}` — Full status page (Jinja2 template, renders initial status card)
- `GET /status/{job_id}/fragment` — HTMX polling fragment (status card partial, no base layout)
- TailwindCSS via CDN (no build step, no Node.js toolchain)
- HTMX via CDN (no npm)
- Templates: `base.html`, `index.html`, `status.html`, `partials/status_card.html`
- Jinja2 auto-escaping enforced (prevents XSS from job data)
- structlog logging for all HTML routes (with `job_id` and `stage`)
- Tests in `tests/test_pages.py` (server-side rendered HTML; no Playwright)

## Out of Scope

- 1 active job per session/IP enforcement (deferred to M007)
- 24h file retention / cleanup reaper (deferred to M007 or M008)
- Upload progress bar with real byte-count (HTMX indicator spinner only)
- Inline upload error display when upload fails (4xx/5xx) — deferred to a future milestone
- Job history page (MVP 2)
- Email notifications
- Real-time SSE (HTMX polling is sufficient for MVP)
- Auth / user accounts
- Real ffmpeg or faster-whisper
- Playwright / browser end-to-end tests
- Custom CSS beyond Tailwind CDN utility classes
- JavaScript beyond what HTMX provides via HTML attributes

---

## Current state after M005

- `POST /jobs/upload` exists, returns `UploadResponse` JSON (201).
- `GET /jobs/{job_id}` exists, returns `JobStatusResponse` JSON (200/404).
- `GET /download/{job_id}` exists, streams ZIP (200/404/409/410/500).
- `app/main.py` includes all three API routers; no Jinja2 or static files wired in.
- No templates directory exists in the project.
- No `GET /` route exists.
- `app/schemas/job.py` has `JobStatusResponse` with: `job_id`, `status`, `original_filename`,
  `video_size_bytes`, `created_at`, `error_message` — all needed by status UI.

---

## Route contract

### `GET /`

Returns: `200 OK`, `Content-Type: text/html`

Renders `index.html` with an upload form. The form:
- `hx-post="/jobs/upload"` + `hx-encoding="multipart/form-data"`
- Shows a loading indicator while upload is in progress (HTMX `hx-indicator`)
- On success: HTMX follows `HX-Redirect` header and navigates to `/status/{job_id}`
- On error (4xx/5xx): HTMX default non-2xx behavior (no swap). The form stays visible.
  Inline error display is **deferred to a future milestone** (see Open Questions #1).

No path parameters. No DB access.

---

### `POST /jobs/upload` (modification only)

Existing behavior unchanged for non-HTMX clients (JSON API remains intact).

**Added behavior when `HX-Request: true` header is present:**

After successful job creation (at the point where JSON `UploadResponse` would be returned),
return instead:

```
HTTP/1.1 200 OK
HX-Redirect: /status/{job_id}
Content-Length: 0
```

HTMX client follows `HX-Redirect` by navigating the browser to `/status/{job_id}`.

**Error handling for HTMX requests:**

In M006, upload errors (413, 415, 422, 503) use HTMX default non-2xx behavior: HTMX does not
swap anything and the form stays visible. No special error fragment is returned. This is
intentionally deferred — inline error display is an Out of Scope item for this milestone
(see Open Questions #1).

Implementation note: detect HTMX via `request.headers.get("HX-Request") == "true"`.
Requires adding `request: Request` parameter to `upload_video()`.

---

### `GET /status/{job_id}`

**Path parameter:** `job_id` — validated as UUID by FastAPI. Non-UUID → 422 automatically.

Returns: `200 OK`, `Content-Type: text/html`

Renders `status.html` (extends `base.html`) containing the initial status card.
The status card is rendered using `partials/status_card.html` (same template used by fragment
endpoint, avoids duplication).

**Error conditions:**

| Condition | HTTP Status | Behavior |
|-----------|-------------|----------|
| `job_id` not a valid UUID | **422** | FastAPI default |
| Job not found in DB | **404** | Raise `HTTPException(404)` |
| Job found | **200** | Render status page |

No 409/410 on the full page — the status card handles those states visually.

---

### `GET /status/{job_id}/fragment`

**Purpose:** HTMX polling target. Returns only the status card HTML fragment (no base layout,
no `<html>`/`<head>`/`<body>` wrappers).

**Path parameter:** `job_id` — UUID, same validation as above.

Returns: `200 OK`, `Content-Type: text/html`

Renders `partials/status_card.html` directly (not via `status.html`).

**Critical HTMX polling behavior:**

The fragment template includes HTMX polling attributes **only when the job is in a
non-terminal state** (`pending`, `queued`, or `processing`):

```html
{# Non-terminal: self-polling card #}
<div id="status-card"
     hx-get="/status/{{ job.job_id }}/fragment"
     hx-trigger="every 2s"
     hx-swap="outerHTML"
     hx-target="this">
  ...
</div>
```

When status is `done` or `failed`, the fragment renders the same card div **without**
`hx-get`/`hx-trigger` attributes. HTMX stops polling automatically.

**Fragment content by status:**

| Status | Card shows |
|--------|-----------|
| `pending` | Spinner + "Preparing job..." + polling active |
| `queued` | Spinner + "Waiting in queue..." + polling active |
| `processing` | Spinner + "Processing video..." + polling active |
| `done` | Checkmark + "Ready" + download button (`/download/{job_id}`) + no polling |
| `failed` | Error icon + "Processing failed" + safe `error_message` (if set) + no polling |

Note: `pending` is rarely seen by users because the `pending → queued` DB transition completes
before `POST /jobs/upload` returns its `HX-Redirect` response. However, `pending` is a valid
DB state and the fragment endpoint must render it correctly rather than treating it as an error.

**Error conditions for fragment endpoint:**

| Condition | HTTP Status |
|-----------|-------------|
| `job_id` not a valid UUID | **422** |
| Job not found | **404** |
| Job found | **200** |

---

## Template structure

```
app/templates/
  base.html                   # HTML shell, Tailwind CDN, HTMX CDN, block content
  index.html                  # Upload form (extends base.html)
  status.html                 # Full status page (extends base.html, includes status_card.html)
  partials/
    status_card.html          # Status card fragment (no layout, used by both status.html and fragment endpoint)
```

### Template data shapes

**`index.html`** — no template context needed (static form).

**`status.html`** context:
```python
{
  "job": JobStatusResponse,   # same schema as JSON API response
  "job_id": str,              # str(job.id) for URL construction in templates
}
```

**`partials/status_card.html`** context (identical):
```python
{
  "job": JobStatusResponse,
  "job_id": str,
}
```

`JobStatusResponse` fields available in templates:
- `job.status` (enum value: `"pending"`, `"queued"`, `"processing"`, `"done"`, `"failed"`)
- `job.original_filename`
- `job.video_size_bytes`
- `job.created_at`
- `job.error_message` (nullable)

---

## Files expected to change during implementation

```
app/api/pages.py              # New: HTML routes (GET /, GET /status/{job_id}, GET /status/{job_id}/fragment)
app/api/jobs.py               # Modified: add HX-Redirect to upload_video() for HTMX requests
app/main.py                   # Modified: add Jinja2Templates setup, include pages_router
app/templates/base.html       # New: HTML shell
app/templates/index.html      # New: upload form
app/templates/status.html     # New: full status page
app/templates/partials/status_card.html  # New: HTMX fragment
tests/test_pages.py           # New: HTML route tests
AI_WORKLOG.md                 # Milestone entry
```

No changes to:
- `app/pipeline/` (no pipeline changes)
- `app/models/` (no schema changes)
- `app/workers/` (no worker changes)
- `alembic/` (no migration)
- `app/api/download.py` (no changes)
- `app/schemas/` (reuse existing `JobStatusResponse`)

---

## Testing requirements

All tests must be deterministic, fast (< 60s), require no real video, no faster-whisper,
and no ffmpeg. Use the existing in-memory DB test fixtures from the project's `conftest.py`.

**File:** `tests/test_pages.py`

Required test cases:

| Test | Setup | Expected |
|------|-------|----------|
| `test_index_returns_html` | no job needed | 200, `Content-Type: text/html`, contains `<form` |
| `test_index_has_htmx_form` | no job needed | form has `hx-post="/jobs/upload"` attribute |
| `test_status_page_valid_job` | queued job in DB | 200, HTML contains job_id |
| `test_status_page_unknown_job` | no job | 404 |
| `test_status_page_invalid_uuid` | non-UUID path | 422 |
| `test_fragment_pending_has_polling` | pending job | 200, HTML contains `hx-get` and `hx-trigger` |
| `test_fragment_queued_has_polling` | queued job | 200, HTML contains `hx-get` and `hx-trigger` |
| `test_fragment_processing_has_polling` | processing job | 200, HTML contains `hx-get` |
| `test_fragment_done_no_polling` | done job | 200, HTML does NOT contain `hx-trigger` |
| `test_fragment_done_has_download_link` | done job | HTML contains `/download/{job_id}` |
| `test_fragment_failed_no_polling` | failed job | 200, HTML does NOT contain `hx-trigger` |
| `test_fragment_failed_shows_error` | failed job with error_message | HTML contains error_message text |
| `test_fragment_unknown_job` | no job | 404 |
| `test_upload_htmx_redirect` | HTMX upload (HX-Request header) | response has `HX-Redirect` header pointing to `/status/{job_id}` |
| `test_upload_non_htmx_unchanged` | normal upload (no HX-Request header) | 201, JSON response (existing behavior unchanged) |
| `test_xss_filename_escaped` | done job with `original_filename="<script>alert(1)</script>.mp4"` | HTML does NOT contain literal `<script>` tag (Jinja2 escaping) |

Total: 16 test cases.

**Testing approach for HTML content:** Use `TestClient` from `httpx`/`starlette.testclient`,
assert on `response.status_code`, `response.headers["content-type"]`, and
`substring in response.text`. Do not use BeautifulSoup or CSS selectors — string assertions
are sufficient and keep the tests simple.

---

## Security requirements

### XSS prevention

Jinja2 auto-escaping must be enabled. FastAPI's `Jinja2Templates` enables auto-escaping for
`.html` files by default. **Verify** this is active; do not disable it.

Fields rendered in templates that come from user-controlled input:
- `job.original_filename` — must be escaped
- `job.error_message` — must be escaped

**Never use `{{ value | safe }}` for any job field.**

### HTMX-specific

- `HX-Redirect` target must be a server-controlled path (`/status/{job_id}`) — never derived
  from a request header or user-supplied value.
- Do not trust `HX-Current-URL` or other HTMX request headers for any logic.
- Fragment endpoint responds to `GET` only (no state changes).

### Path parameter safety

- `job_id` in URL is validated as `UUID` by FastAPI — non-UUID → 422 before handler runs.
- `job_id` is used only for DB lookup, never for filesystem path construction (no FS access in
  pages routes).

### No new attack surface

- Upload form sends `multipart/form-data` to existing `/jobs/upload`. No new upload handling.
- HTML routes read from DB, never write.
- No subprocess calls.
- No user-supplied URL redirects.

---

## Docker / manual gate

```powershell
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app worker

# Health check
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json

# Upload form smoke
$r = Invoke-WebRequest http://localhost:8000/
$r.StatusCode     # Expected: 200
$r.Content -match '<form'   # Expected: True

# Upload via browser: open http://localhost:8000/, upload a small MP4, observe redirect to /status/{job_id}
# Status page smoke (after upload, replace {JOB_ID} with actual UUID):
$r = Invoke-WebRequest "http://localhost:8000/status/{JOB_ID}"
$r.StatusCode     # Expected: 200

# Fragment smoke (queued or processing state):
$r = Invoke-WebRequest "http://localhost:8000/status/{JOB_ID}/fragment"
$r.StatusCode     # Expected: 200
$r.Content -match 'hx-trigger'   # Expected: True (while non-terminal)

# After job completes (done state):
$r = Invoke-WebRequest "http://localhost:8000/status/{JOB_ID}/fragment"
$r.Content -match 'hx-trigger'   # Expected: False (polling stopped)
$r.Content -match '/download/'   # Expected: True (download link present)

# 404 smoke:
try {
  Invoke-WebRequest "http://localhost:8000/status/00000000-0000-0000-0000-000000000000" -ErrorAction Stop
} catch {
  [int]$_.Exception.Response.StatusCode   # Expected: 404
}
```

---

## Acceptance criteria

**AC1** - `GET /` returns 200 HTML containing an upload form with `hx-post="/jobs/upload"`.

**AC2** - `POST /jobs/upload` with `HX-Request: true` header returns 200 with
`HX-Redirect: /status/{job_id}` header; response body may be empty.

**AC3** - `POST /jobs/upload` without `HX-Request` header continues to return 201 JSON
`UploadResponse` — existing API behavior unchanged.

**AC4** - `GET /status/{job_id}` returns 200 HTML for a valid job.

**AC5** - `GET /status/{job_id}` returns 404 for an unknown job UUID.

**AC6** - `GET /status/{job_id}` returns 422 for a non-UUID path value.

**AC7** - `GET /status/{job_id}/fragment` for a `pending`, `queued`, or `processing` job
returns HTML containing `hx-get="/status/{job_id}/fragment"` and `hx-trigger="every 2s"`.

**AC8** - `GET /status/{job_id}/fragment` for a `done` job returns HTML that:
- does NOT contain `hx-trigger`
- contains a link to `/download/{job_id}`

**AC9** - `GET /status/{job_id}/fragment` for a `failed` job returns HTML that:
- does NOT contain `hx-trigger`
- contains the `error_message` value (if non-null) — HTML-escaped

**AC10** - `original_filename` containing `<script>` renders as escaped text in all templates;
no literal `<script>` tag appears in any HTML response for that job.

**AC11** - `HX-Redirect` value is always `/status/{job_id}` — a server-constructed path.
It is never derived from request headers or user input.

**AC12** - No Alembic migration introduced.

**AC13** - No changes to `app/models/`, `app/pipeline/`, `app/workers/`, or
`app/api/download.py`.

**AC14** - `tests/test_pages.py` contains all 16 required test cases and passes.
`pytest tests/ -v` completes under 60 seconds.

**AC15** - All existing M001–M005 tests remain green.

**AC16** - Security review by `security-agent` completed in clean context before merge;
no High or Critical open findings remain.

**AC17** - Manual smoke: open `http://localhost:8000/` in a browser, upload a video,
confirm redirect to status page, confirm HTMX polling updates the card, confirm download
button appears on completion.

---

## Quality gates

| Gate | Required | Notes |
|------|----------|-------|
| Functional (manual test) | Always | Full browser flow: upload → status page → download |
| Tests (pytest green) | Always | All tests including regression (M001–M005) |
| Security review | Always | `security-agent`, clean context, diff + ACs |
| Human understanding | Always | Reviewer can explain HTMX polling stop mechanism |
| DB migration | N/A | No migration needed |
| Performance | N/A | Static templates, no heavy queries |
| Documentation | N/A | No interface contract change; no ADR needed |

---

## Implementation order

1. Add `app/templates/` directory; create `base.html` with Tailwind + HTMX CDN includes.
2. Create `index.html` (upload form, extends `base.html`).
3. Create `app/api/pages.py` with `GET /` route; wire into `app/main.py` with
   `Jinja2Templates(directory="app/templates")`.
4. Verify `GET /` returns 200 HTML manually.
5. Modify `app/api/jobs.py` — add `Request` parameter to `upload_video()`, add
   `HX-Redirect` branch for HTMX uploads.
6. Create `partials/status_card.html` (the reusable fragment; handles all 5 statuses).
7. Create `status.html` (extends `base.html`, includes `status_card.html`).
8. Add `GET /status/{job_id}` and `GET /status/{job_id}/fragment` to `pages.py`.
9. Write `tests/test_pages.py` (all 16 test cases).
10. Run `pytest tests/ -v` — must be green.
11. Run Docker canonical gate + full browser smoke (upload → status → download).
12. Update `AI_WORKLOG.md`.
13. Activate `security-agent` in clean context (diff + ACs).
14. Activate Codex Reviewer in clean context (diff + ACs).
15. Merge after all gates green.

---

## Open questions / deferred risks

**1. HTMX upload error display — deferred.**
In M006, upload errors (413, 415, 422, 503) leave the form visible with no inline message
(HTMX default non-2xx behavior: no swap). This is acceptable for MVP since error cases are
rare (file too large, wrong type) and the user can retry by refreshing. A future milestone
should add an `#upload-error` div, return a small HTML fragment from the upload endpoint on
4xx, and swap it into the form via `hx-target="#upload-error"`. This requires no architectural
change — it is purely additive UI work.

**2. HTMX polling interval.**
2 seconds is the default recommendation. If the mock pipeline finishes in < 1s (in tests),
manual smoke still works because the status page loads the current state on initial render.
Adjust interval in a later milestone if UX is poor with real processing times.

**3. Jinja2Templates directory path (Docker vs local).**
`Jinja2Templates(directory="app/templates")` works when the working directory is the repo
root. In Docker, verify the `WORKDIR` in `Dockerfile` is `/app` and the templates dir is
mounted correctly. If the Docker path differs, use `Path(__file__).parent.parent / "templates"`
for a path relative to the module file.

**4. `done` job with expired ZIP.**
If a job is `done` but `expires_at` is in the past, the status page shows the download button,
but `/download/{job_id}` returns 410. M006 does not need to show a special "expired" state —
the user will see the 410 response when clicking download. A dedicated expired state can be
added in a later milestone.

**5. Multiple open tabs / race on HX-Redirect.**
If a user opens two tabs and uploads from both, both get redirected to their own `/status/{job_id}`.
No state conflict. The 1-active-job-per-session limit (deferred to M007) is not enforced in M006,
so duplicate uploads are allowed in this milestone.

**6. Static files directory.**
M006 does not require a static files directory (Tailwind and HTMX are loaded from CDN). If
future milestones need local CSS/JS, add `StaticFiles` mounting to `main.py` at that point.
Do not add it preemptively in M006.
