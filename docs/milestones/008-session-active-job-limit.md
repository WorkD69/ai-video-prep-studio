# Milestone 008 — 1 Active Job per Session

## Summary

Enforce the MVP hard limit "1 active job per session" using the signed-cookie session mechanism from
**ADR 004** and a **PostgreSQL transaction-level advisory lock** for race-safety.

After M008:
- Each browser carries a stable signed session cookie (`aivps_session`); `jobs.session_id` is derived
  from it instead of a per-upload `uuid4()`.
- A new upload is rejected with **HTTP 429** when the session already has an *active* job
  (status in `pending` / `queued` / `processing`).
- The limit check is **atomic**: an advisory `pg_advisory_xact_lock` keyed on the session serializes
  concurrent uploads of the same session, so parallel requests cannot both create a job (no TOCTOU).
- For HTMX requests the 429 renders an **inline error fragment** (via the `response-targets`
  extension) with a link to the already-active job's status page; the page does not redirect.
- For non-HX/JSON clients the 429 returns `{"detail": "active_job_exists"}`.

**Prerequisite:** ADR 004 is Accepted (human sign-off granted 2026-06-07).

## Branch naming

- **Spec (docs-only, current):** `docs/milestone-008-session-limit`
- **Implementation:** `feature/milestone-008-session-limit`

## Responsible agents

- Implementation: `backend-agent`
- Test writing: `qa-agent`
- Security review: `security-agent` (required before merge — see Quality Gates)

---

## Definitions

- **Active job** = a job whose `status` is in `{pending, queued, processing}` (the existing
  `_NON_TERMINAL` set in `app/api/pages.py`). `done` and `failed` are terminal and do not count.
- **Session** = the signed-cookie session from ADR 004 (`session_id`, a UUID).
- **Limit** = exactly **1** active job per session (hardcoded for MVP; not configurable).

---

## In Scope

### 1. Session cookie helpers — new `app/services/session.py` (stdlib HMAC, no new dependency)
- `sign_session_id(session_id) -> str` — return `"{session_id}.{b64url(HMAC-SHA256(SECRET_KEY,
  session_id))}"` using stdlib `hmac`/`hashlib`/`base64` only.
- `read_session_id(request) -> str | None` — split the cookie on the last `.`, recompute the HMAC,
  compare with `hmac.compare_digest`; return the raw session_id, or `None` if **absent / tampered /
  invalid** (never raises). There is no server-side expiry check — an HMAC-valid cookie is always
  accepted; expiry is handled browser-side by `Max-Age`.
- `mint_session_id() -> str` — new `uuid4()` string.
- `set_session_cookie(response, session_id) -> None` — `response.set_cookie(name=settings.
  session_cookie_name, value=sign_session_id(session_id), max_age=settings.session_cookie_max_age,
  httponly=True, samesite="lax", secure=settings.session_cookie_secure, path="/")`.
- `resolve_session(request) -> tuple[str, bool]` — convenience: returns `(session_id, is_new)`,
  minting if `read_session_id` is `None`. Used by both the upload endpoint and the index page.

### 2. Active-job limit helpers — new `app/services/active_job.py`
- `session_lock_key(session_id: str) -> int` — deterministic **signed 64-bit** int for the advisory
  lock (e.g. `int.from_bytes(blake2b(session_id, digest_size=8).digest(), "big", signed=True)`).
  Pure + directly unit-tested.
- `acquire_session_lock(db, session_id) -> None` — `db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
  {"k": session_lock_key(session_id)})`. Auto-released at the transaction's commit/rollback.
- `find_active_job(db, session_id) -> Job | None` — `SELECT ... WHERE session_id = :sid AND status IN
  (pending, queued, processing) LIMIT 1`.

These two helpers are **seams**: the upload endpoint imports them, and unit tests patch
`app.api.jobs.find_active_job` / `app.api.jobs.acquire_session_lock` (real SQL is exercised by the
Docker gate, since CI has no Postgres).

### 3. Upload endpoint changes — `app/api/jobs.py`
Replace `session_id = str(uuid4())` with the cookie-resolved session and add the limit. Final order:

```
1. validate content_type / extension / magic bytes      (unchanged)
2. session_id, is_new = resolve_session(request)
3. EARLY pre-check (no lock): if find_active_job(db, session_id): -> 429   (fast-fail, no file saved)
4. stream_save(file, ...)                                (unchanged)
5. acquire_session_lock(db, session_id)                  (advisory xact lock)
6. AUTHORITATIVE re-check: if find_active_job(db, session_id):
       dest_path.unlink(missing_ok=True); db.rollback(); -> 429           (rare race; delete saved file)
7. build Job(session_id=session_id, ...); db.add; db.commit()  -> releases lock; job now visible
8. enqueue + compensation                                (unchanged)
9. guarded flip pending -> queued                        (unchanged)
10. set_session_cookie on the ACTUAL returned object (per cookie contract; all branches, no is_new gate)
11. return UploadResponse / HX-Redirect / JSONResponse(429) / HTMLResponse(429)
```

- The 429 branch returns:
  - **HX-Request** → `HTMLResponse(<rendered partials/upload_error.html>, status_code=429)` (the
    `response-targets` ext swaps it into `#upload-error`). Fragment links to `/status/{active_job_id}`.
  - **non-HX** → `JSONResponse(status_code=429, content={"detail": "active_job_exists"})` (returned,
    not raised — see cookie contract below).
- `find_active_job` returns the active `Job`, so its `id` is available for the status-page link.

#### Cookie-setting contract (explicit — set on the object actually returned)

`set_cookie` only takes effect on the **response object that is actually returned**. FastAPI's
injected `response: Response` parameter is merged **only** into a default (model/dict) return; it is
**not** applied when the handler explicitly returns its own `Response`/`HTMLResponse`/`JSONResponse`,
and it is **not** applied at all if the handler `raise`s `HTTPException`. Therefore the cookie must be
set on each concrete returned object. The endpoint must **not** use `raise HTTPException` on any path
where a cookie may need to be set — it returns explicit response objects instead.

| Return branch | Returned object | Cookie action |
|---|---|---|
| JSON 201 success | inject `response: Response`; return `UploadResponse` model | `set_session_cookie(response, session_id)` (the injected response is merged into the model return) |
| HTMX success | `Response(status_code=200, headers={"HX-Redirect": ...})` | `set_session_cookie(that_response, session_id)` before returning |
| HTMX 429 limit | `HTMLResponse(fragment, status_code=429)` | `set_session_cookie(that_htmlresponse, session_id)` before returning |
| non-HX 429 limit | `JSONResponse(status_code=429, content=...)` | `set_session_cookie(that_jsonresponse, session_id)` before returning |

Note: on the 429 branches `is_new` is logically always `False` (a freshly minted session has zero
jobs and cannot be blocked), so the cookie almost always already exists; setting it again is harmless
(idempotent refresh) and keeps every branch uniform. Always set the cookie — do not gate on `is_new`.

### 4. Index page sets the cookie — `app/api/pages.py`
- `GET /` ensures the session cookie exists before the first upload: `resolve_session(request)` then
  `set_session_cookie(template_response, session_id)`. Set on the **returned `TemplateResponse`
  object** (not on an injected `response`). Set unconditionally (idempotent refresh), per the cookie
  contract above.

### 5. Template changes
- `app/templates/base.html` (wherever HTMX core is loaded): add the `htmx-ext-response-targets` CDN
  script.
- `app/templates/index.html`: add `hx-ext="response-targets"` and `hx-target-429="#upload-error"` on
  the form; add `<div id="upload-error" class="..."></div>` below the form.
- `app/templates/partials/upload_error.html` — new fragment: friendly message + link to the active
  job (`/status/{active_job_id}`).

### 6. Config — `app/config.py`
| Setting | Default | Env | Purpose |
|---|---|---|---|
| `session_cookie_name` | `"aivps_session"` | `SESSION_COOKIE_NAME` | Cookie name |
| `session_cookie_max_age` | `2592000` (30 d) | `SESSION_COOKIE_MAX_AGE` | Cookie lifetime (s) |
| `session_cookie_secure` | `False` | `SESSION_COOKIE_SECURE` | `True` behind HTTPS (prod) |

`SameSite=Lax` and `HttpOnly=True` are hardcoded (not env-tunable in MVP).

### 7. Alembic migration — partial index
- New revision: partial index for the hot active-job query.
  `CREATE INDEX ix_jobs_active_session ON jobs (session_id) WHERE status IN ('pending','queued','processing')`.
- `down_revision = 'd4a8b3c9f012'` (current head). `downgrade()` drops the index.

### 8. Tests — see Testing Requirements.

### 9. `AI_WORKLOG.md` — milestone entry.

### 10. Update agent card — `docs/agents/backend-agent.md`
The current card documents `session_id` as "server-generated anonymous UUID4 per upload ... do NOT
read from cookies". M008 **changes that contract**: after M008, `session_id` is the signed-cookie
browser session id (ADR 004), no longer a fresh uuid per upload. Update the `session_id` description
in both the `jobs` table section and the "Security Responsibilities" section to reference ADR 004 and
state that `session_id` now comes from the signed `aivps_session` cookie (still not from a raw,
unsigned user header). This is part of M008 implementation, not deferred.

---

## Files expected to change during implementation

```
app/services/session.py            # New: HMAC cookie sign/read/set, resolve_session
app/services/active_job.py         # New: session_lock_key, acquire_session_lock, find_active_job
app/api/jobs.py                    # Modified: cookie session, early+locked checks, 429 branches, cookie on returned objs
app/api/pages.py                   # Modified: GET / sets session cookie
app/config.py                      # Modified: session cookie settings (name, max_age, secure)
app/templates/base.html            # Modified: htmx-ext-response-targets CDN script
app/templates/index.html           # Modified: hx-ext, hx-target-429, #upload-error div
app/templates/partials/upload_error.html  # New: 429 inline fragment with status link
alembic/versions/<rev>_add_active_job_index.py  # New: partial index ix_jobs_active_session
docs/agents/backend-agent.md       # Modified: session_id contract -> ADR 004 (signed cookie)
tests/test_session.py              # New: HMAC sign/verify, resolve, cookie attrs, lock key
tests/test_job_limit.py            # New: limit enforcement (patched seams), HX/JSON 429, race-reject
tests/test_upload.py               # Modified: patch find_active_job; happy paths still green
AI_WORKLOG.md                      # Milestone entry
```

No new entries in `requirements.txt` — signing uses the Python stdlib only.

---

## Out of Scope

- IP-based limiting / `X-Forwarded-For` / reverse-proxy trust (ADR 004; MVP2 hardening).
- Authenticated user sessions / login (the `users` table stays a stub; limit keys on cookie session).
- Per-IP or global rate limiting (MVP2 "Rate limiting hardening").
- Preventing limit bypass via clearing cookies / incognito — **accepted** MVP trade-off (ADR 004).
- Configurable limit `N > 1` per session (hardcoded `1`).
- Server-side cookie expiry / timestamped tokens — expiry is browser-side via `Max-Age` (ADR 004).
- Distributed locking beyond the single Postgres (the one Postgres is the lock authority; correct for
  the MVP single-DB deploy).
- Session-based **authorization** for viewing/downloading jobs — job access stays by `job_id` UUID
  with no session check (unchanged from M005/M006).
- Marking stuck `pending`/`queued` jobs as `failed` (a separate reliability concern; see Risks).
- HTTPS / `Secure=True` rollout (arrives with MVP2 TLS).

---

## Current state before M008

- `session_id = str(uuid4())` per upload, never persisted to the browser
  (`app/api/jobs.py`). No cookie is set anywhere.
- `_NON_TERMINAL = {pending, queued, processing}` already defined in `app/api/pages.py`.
- `get_db` yields one Session per request; the endpoint already does multiple commits.
- HTMX form posts to `/jobs/upload`; success returns an `HX-Redirect` header.
- No advisory-lock usage anywhere; single Alembic revision (`d4a8b3c9f012`) is head.
- All M001–M007 tests green.

---

## Atomicity / race design (the core)

```
# Single transaction on the request's db session (READ COMMITTED — Postgres default)
acquire_session_lock(db, session_id)         # SELECT pg_advisory_xact_lock(:key) — waits if held
active = find_active_job(db, session_id)      # re-read; sees latest committed jobs (READ COMMITTED)
if active:
    delete saved file; db.rollback()          # rollback releases the advisory lock
    return 429
db.add(job); db.commit()                      # commit releases the lock; new job now visible
```

- The advisory **xact** lock is held from `acquire_session_lock` until the first `commit`/`rollback`
  on the same connection — exactly spanning the check→insert window. No manual unlock, no leak.
- Concurrent same-session requests serialize on the same `session_lock_key`. The first commits its
  job; the second acquires the lock, the re-check now finds the active job, and it is rejected.
- Different sessions hash to different keys → no cross-session blocking.
- The **early pre-check** (step 3, no lock) is a bandwidth/UX optimization (fast-fail before a 500 MB
  upload). The **locked re-check** (step 6) is the correctness boundary. If an implementer simplifies,
  the locked re-check is mandatory; the early pre-check is recommended.
- Lock key is a signed 64-bit int because `pg_advisory_xact_lock(bigint)` expects a signed 8-byte int.
  Hash collisions only cause rare benign contention between two unrelated sessions; the exact-match
  `WHERE session_id = :sid` keeps the limit itself correct.

---

## Testing requirements

All tests deterministic, fast, no real video, no network, **no real Postgres** (CI uses MockDB /
mocks). Limit helpers are patched at the `app.api.jobs` import site.

**New file `tests/test_session.py`:**
| Test | Expected |
|---|---|
| `test_sign_verify_roundtrip` | `read_session_id` recovers the id signed by `set_session_cookie` |
| `test_tampered_cookie_returns_none` | corrupted cookie value → `read_session_id` returns `None`, no raise |
| `test_missing_cookie_returns_none` | no cookie → `None` |
| `test_resolve_mints_when_absent` | `resolve_session` returns `(uuid, is_new=True)` |
| `test_set_cookie_attributes` | cookie set with HttpOnly, SameSite=Lax, configured Max-Age/Secure |
| `test_session_lock_key_deterministic` | `session_lock_key(x)` stable and in signed int64 range |

Note: there is **no** "expired cookie" test — the HMAC carries no timestamp, so an HMAC-valid cookie
is always accepted regardless of age (expiry is browser-side via `Max-Age`). Do not assert that the
server rejects an old-but-valid cookie.

**New file `tests/test_job_limit.py`** (patch `find_active_job` / `acquire_session_lock`):
| Test | Setup | Expected |
|---|---|---|
| `test_upload_blocked_when_active_job_json` | `find_active_job` → a fake active Job; non-HX | 429, `detail=active_job_exists`, no `db.add`, no file retained |
| `test_upload_blocked_when_active_job_htmx` | active Job; `HX-Request: true` | 429, HTML fragment body, contains `/status/{active_id}`, no `HX-Redirect` |
| `test_upload_allowed_when_no_active_job` | `find_active_job` → `None` | 201, job created (existing happy path holds) |
| `test_upload_allowed_when_previous_job_terminal` | `find_active_job` → `None` (done/failed not matched) | 201 |
| `test_lock_acquired_before_authoritative_check` | spies on `acquire_session_lock` & `find_active_job` | lock called before the *second* (authoritative) check |
| `test_race_reject_deletes_saved_file` | early check `None`, locked re-check → active | saved input file removed; `db.rollback` called; 429 |
| `test_cookie_set_for_new_session` | request without cookie | response sets `aivps_session` |
| `test_session_id_from_cookie_used` | request **with** a valid signed cookie | created job's `session_id` == cookie's id (not a fresh uuid) |

**Update `tests/test_upload.py`:** existing happy-path tests must still pass. They post without a
cookie → a session is minted and the cookie is set; `find_active_job` must be patched/return `None`
so uploads proceed. The `UUID(data["session_id"])` assertion still holds (session_id is still a UUID).

**Do NOT** unit-test the real advisory-lock serialization (needs Postgres) — that is the Docker gate.

---

## Security review focus (security-agent, clean context)

Map to `security-agent.md` §6 (Rate Limiting) + cookie handling:
1. **Atomicity / TOCTOU** — advisory xact lock truly serializes same-session uploads; parallel
   requests cannot both create a job. (§6 "atomic", "no bypass via parallel requests")
2. **Server-side enforcement** — limit enforced in the endpoint, not client-only. (§6)
3. **Cookie integrity** — signed with `SECRET_KEY`; absent/tampered/invalid cookie → fresh session,
   never a 500, never impersonation. (§6 "signed cookie or HMAC")
4. **`X-Forwarded-For` / IP not used** — confirm no IP path in limiting. (§6)
5. **Saved-file cleanup on reject** — race-reject deletes the just-saved input (no orphan growth in
   `uploads/`), reusing the existing `dest_path.unlink(missing_ok=True)` pattern.
6. **No secret leakage** — the signed cookie value is never logged; only `session_id` (random UUID)
   may appear in structlog.
7. **Accepted residual risk (documented, not a finding)** — limit is bypassable by clearing cookies /
   incognito; disk-fill via scripted fresh cookies is possible. Mitigations (IP rate-limit behind
   trusted proxy, auth) are MVP2. ADR 004 records this as an accepted MVP trade-off.

---

## Docker / manual gate (canonical — DB milestone)

```powershell
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app worker

Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json
# Expected: status=ok, db=ok, redis=ok

# 1. Open / in a browser -> DevTools shows cookie `aivps_session` (HttpOnly).
# 2. Upload a small MP4 -> redirect to /status/{id}; job is active.
# 3. Before it finishes, upload again in the SAME browser -> inline 429 error appears in #upload-error
#    with a link to the active job's status page; NO redirect.
# 4. Open an incognito window (different session) -> upload succeeds.
# 5. Wait for the first job to reach done -> upload again same browser -> accepted (201).
# 6. Manually corrupt the cookie value in DevTools -> upload -> fresh session minted, upload accepted,
#    no 500.
# 7. Concurrency: fire two simultaneous uploads with the SAME cookie (e.g. two `curl -b cookie ...`):
#    exactly one returns 201, the other 429. uploads/ has no orphan file from the rejected one.
```

Record outcomes in `AI_WORKLOG.md`.

---

## Acceptance criteria

**AC1** — A signed `aivps_session` cookie is set on first visit to `/` (or first upload) and reused
on subsequent requests (HttpOnly, SameSite=Lax, Max-Age per config).

**AC2** — An **absent / tampered / invalid** cookie is rejected silently and a fresh session is
minted; the request never returns 500 because of the cookie. (There is no server-side "expired"
rejection — an HMAC-valid cookie is always accepted; expiry is enforced browser-side via `Max-Age`.)

**AC3** — `jobs.session_id` is populated from the cookie session, not from a per-upload `uuid4()`.

**AC4** — With an active job (`pending`/`queued`/`processing`) for the session, a new upload returns
**429**; no new job row is created and no input file is retained.

**AC5** — When the session's previous job is terminal (`done`/`failed`), a new upload is accepted (201).

**AC6** — The authoritative limit check runs under a `pg_advisory_xact_lock` keyed on the session;
two concurrent same-session uploads yield exactly one accepted job (no TOCTOU bypass).

**AC7** — Different sessions do not block each other (distinct lock keys; concurrent uploads from
different sessions both succeed).

**AC8** — For HX requests, the 429 renders an inline fragment in `#upload-error` (via
`response-targets`), including a link to the active job's status page; the page does not redirect.

**AC9** — For non-HX/JSON requests, the 429 returns `{"detail": "active_job_exists"}`.

**AC10** — On a race-reject after the file was saved, the saved input file is deleted (no orphan in
`uploads/`).

**AC11** — Client IP / `X-Forwarded-For` is NOT used for limiting; ADR 004 documents the accepted
cookie-bypass trade-off.

**AC12** — Alembic migration adds `ix_jobs_active_session`; `alembic upgrade head` and `downgrade`
both succeed against the canonical Docker DB gate.

**AC13** — All existing M001–M007 tests remain green; `tests/test_session.py` and
`tests/test_job_limit.py` pass. `pytest tests/ -v` completes under 60s.

**AC14** — Security review by `security-agent` completed in clean context (diff + ACs); no High/Critical
open findings.

**AC15** — Docker manual gate executed and recorded in `AI_WORKLOG.md` (concurrency: one 201 + one 429;
incognito succeeds; tampered cookie → fresh session, no 500).

---

## Quality gates

| Gate | Required | Notes |
|---|---|---|
| Functional (manual) | Always | Steps 1–7 of the Docker gate |
| Tests (pytest green) | Always | New session + limit tests + M001–M007 regression |
| Security review | Always | `security-agent`, clean context; TOCTOU + cookie integrity are critical |
| Human understanding | Always | Reviewer can explain why advisory **xact** lock spans check→commit, and why the limit is a soft control |
| DB migration | Always | Canonical gate: `alembic upgrade head` on Docker DB; verify index exists; `downgrade` works |
| Performance | N/A | Upload path; partial index keeps the active-job query cheap |
| Reliability | Contextual | Stuck-pending edge case noted in Risks |
| Documentation | Always | ADR 004 + this spec are interface-affecting (session contract changes) |

---

## Implementation order

1. `app/config.py` — add the three session cookie settings.
2. `app/services/session.py` — stdlib-HMAC cookie sign/read/set + `resolve_session`. Unit-test in `test_session.py`.
3. `app/services/active_job.py` — `session_lock_key`, `acquire_session_lock`, `find_active_job`.
4. `tests/test_session.py` — green.
5. `app/api/jobs.py` — wire cookie session + early check + locked check + 429 branches + cookie set +
   race-reject file delete.
6. `app/api/pages.py` — set cookie on `GET /`.
7. Templates — `base.html` (ext script), `index.html` (ext attrs + error div),
   `partials/upload_error.html`.
8. `tests/test_job_limit.py` + update `tests/test_upload.py` (patch `find_active_job`). `pytest tests/ -v` green.
9. Alembic revision for `ix_jobs_active_session` (down_revision = `d4a8b3c9f012`).
10. Canonical Docker DB gate + manual smoke (steps 1–7).
11. `AI_WORKLOG.md` entry.
12. `security-agent` clean-context review (diff + ACs).
13. Codex Reviewer clean-context review (diff + ACs).
14. Human sign-off → merge. (Max 2 reviewer-fix cycles per CLAUDE.md.)

---

## Open questions / deferred risks

1. **Stuck `pending`/`queued` blocks the session.** If a job never advances (worker crash before
   enqueue/pickup), it stays "active" and blocks new uploads until the 24h cleanup deletes its files —
   but the **DB row keeps its non-terminal status**, so the session stays blocked even after files are
   gone. M007 cleanup deletes files, not rows/status. *MVP stance:* rare; acceptable. *Mitigation
   (deferred):* a reaper that marks expired non-terminal jobs `failed`. Flagged for a reliability
   milestone.
2. **Limit bypass by clearing cookies / incognito.** Accepted MVP trade-off (ADR 004). Hard controls
   = MVP2 (IP rate-limit behind trusted proxy) / auth.
3. **`Secure=False` over HTTP.** Cookie sent in clear on the MVP HTTP deploy. Must become `Secure=True`
   with MVP2 HTTPS. Config flag is ready.
4. **No Postgres in CI.** Advisory-lock serialization can't be unit-tested in CI; covered by the
   Docker concurrency gate. Helpers are patched seams in unit tests.
5. **Lock key collisions.** Two unrelated sessions sharing a 64-bit hash → rare benign contention
   only; the exact-match `WHERE session_id` keeps correctness.
6. **Multiple app instances.** All app containers share one Postgres → the advisory lock is global
   across instances. Correct for the single-DB MVP deploy. Multi-DB/sharding is out of scope.
7. **No server-side cookie expiry.** An HMAC-valid cookie is accepted indefinitely as long as the
   browser sends it (browser drops it at `Max-Age`). `SECRET_KEY` rotation is the blunt invalidation
   lever if ever needed. Accepted for MVP (ADR 004).
