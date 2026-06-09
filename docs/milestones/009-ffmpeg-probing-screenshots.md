# Milestone 009 — Real ffmpeg/ffprobe Probing + Screenshot Extraction

## Summary

Replace the M004 mock media pipeline with real media processing:

- **ffprobe** determines the real video duration and enforces the **60-minute limit at upload**
  (before enqueue), persisting `duration_seconds` on the job.
- **ffmpeg** extracts real screenshots every 20 seconds into the ZIP package, replacing the
  placeholder JPEGs.
- Transcription **stays `MockTranscriber`** — real faster-whisper is M010 (audio extraction is also
  deferred to M010).

Media tools sit behind `MediaProber` / `ScreenshotExtractor` interfaces with real ffprobe/ffmpeg
implementations and **fake** implementations for CI — the same swappable-backend pattern as
`Transcriber` / `MockTranscriber` (ADR 003 / ADR 005). The ZIP layout, manifest schema,
`frame_NNNNNNs.jpg` naming, global-timecode formula, and the `0.6` silence threshold are **unchanged**;
only the screenshot **producer** changes and `duration_seconds` becomes real.

**Prerequisite:** ADR 005 is Accepted (human sign-off granted 2026-06-09).

## Branch naming

- **Spec (docs-only, current):** `docs/milestone-009-ffmpeg-probing-screenshots-spec`
- **Implementation:** `feature/milestone-009-ffmpeg-probing-screenshots`

## Responsible agents

- Implementation: `media-pipeline-agent` (+ `backend-agent` for the upload-endpoint change)
- Test writing: `qa-agent`
- Security review: `security-agent` (required before merge — subprocess + path handling)

---

## Locked decisions (human-approved, 2026-06-09)

| # | Decision |
|---|---|
| D1 | Duration limit enforced **at upload** via synchronous ffprobe, before enqueue; reject + delete file; `duration_seconds` written to the job. |
| D2 | Real audio extraction (`ffmpeg -vn`) **deferred to M010**; M009 keeps `MockTranscriber` on placeholder audio. |
| D3 | Screenshots via **single-pass `fps=1/20`** + rename + reconciliation with the manifest. |
| D4 | **ADR 005** records the media-processing interface + duration-enforcement point (Accepted). |

---

## In Scope

### 1. Media interfaces — new `app/pipeline/media.py`

```python
class MediaError(Exception):
    """Controlled media-processing failure with a safe, user-facing message."""

class MediaProber(Protocol):
    def probe_duration(self, input_path: str) -> float: ...

class ScreenshotExtractor(Protocol):
    def extract(self, input_path: str, out_dir: Path, timestamps: list[int]) -> list[Path]: ...
```

- `FfprobeMediaProber.probe_duration` →
  `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 <path>`;
  parse float; raise `MediaError("invalid_or_unreadable_media")` on non-zero exit, empty/unparsable
  output, NaN/inf, or duration ≤ 0. Timeout ~30 s. (`--` optional — see Safe subprocess policy.)
- `FfmpegScreenshotExtractor.extract` (single-pass, D3):
  `ffmpeg -hide_banner -loglevel error -i <path> -vf fps=1/20 -q:v 2 -f image2 <tmp>/raw_%06d.jpg`,
  then map raw frame `k` (1-based) → second `(k-1)*20`; rename to `frame_{sec:06d}s.jpg`;
  **reconcile to the passed `timestamps`** — keep exactly the requested-second frames, drop any extra
  trailing frame, a missing one → `MediaError("screenshot_extraction_failed")`. Return a sorted list
  of `Path`.
- `FakeMediaProber(duration)` — returns the configured value.
- `FakeScreenshotExtractor` — writes the placeholder JPEG bytes to `frame_{sec:06d}s.jpg` for each
  requested timestamp; returns the paths. Deterministic, no ffmpeg.
- `get_media_backend() -> tuple[MediaProber, ScreenshotExtractor]` selected by
  `settings.media_backend`; CI/tests use `MEDIA_BACKEND=fake`.

### 2. Shared timestamp helper

- `screenshot_timestamps(span: float) -> list[int]` = `0, 20, 40, … while t < span` (span > 0).
  **Single source of truth** for the manifest and the extractor → the invariant
  `metadata.screenshot_count == manifest rows == files in screenshots/` holds by construction.
- `build_screenshots_manifest_csv` is refactored to consume this list (keeps the existing CSV schema).

### 3. Real worker pipeline — new `app/pipeline/media_pipeline.py`

`run_media_pipeline(job, prober, extractor) -> Path` (replaces `run_mock_output_pipeline` in the
worker success path; the mock orchestration may be generalised rather than duplicated):

1. Resolve and validate `job.input_path` under `upload_dir`; otherwise `MediaError` → fail the job.
2. `span = job.duration_seconds` (defensive: probe in the worker if `None`).
3. `timestamps = screenshot_timestamps(span)`.
4. `frames = extractor.extract(job.input_path, staging/screenshots, timestamps)`.
5. `manifest = build_screenshots_manifest_csv(timestamps=timestamps, ...)` — from the same list.
6. Transcription: **`MockTranscriber`** (M009; real transcription is M010).
7. `build_zip(..., screenshots_dir=staging/screenshots, screenshot_count=len(frames))`.
8. `try/finally` staging cleanup (existing pattern).

> ffmpeg note: `%06d` is the frame **index**, not seconds; the `second=(k-1)*20` rename is mandatory
> (see `docs/agents/media-pipeline-agent.md`). Exact frame→second mapping is confirmed by the
> real-binary smoke test.
>
> The transcript stays mock (0–75 s) while `span` is the real duration — an intentional, documented
> inconsistency until M010. The screenshot invariant still holds strictly.

### 4. ZIP packaging — `app/pipeline/zip_packaging.py`

`build_zip` gains an optional `screenshots_dir: Path | None = None`:

- provided → add the real `frame_NNNNNNs.jpg` files from the directory (sorted), reading real bytes;
  assert `len(files) == screenshot_count` (invariant guard).
- `None` → existing behaviour (placeholder JPEGs by count) — **all M004 tests stay green**.

Unchanged: the 6 file names, the `screenshots/` directory entry, manifest schema
`frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id`, `frame_NNNNNNs.jpg` naming, the
global-timecode formula, and the `0.6` threshold. `metadata.json` now carries the real
`duration_seconds`; `transcriber` stays `MockTranscriber`, `model_size` stays `mock` (until M010).

### 5. Upload-time duration enforcement — `app/api/jobs.py`

After `stream_save`, **before** `acquire_session_lock` / job creation:

```
1. duration = prober.probe_duration(str(dest_path))   # prober from get_media_prober(); fake in CI/tests
2. MediaError (corrupt/unreadable, even though magic-bytes passed) -> delete file -> 400
3. duration > settings.max_duration_seconds (3600)    -> delete file -> 422 "Video exceeds the 60-minute limit"
4. duration == 3600 exactly                           -> allowed (strict >)
5. success                                            -> create job with duration_seconds=duration
```

The file is deleted before any job row is created (no orphan rows, no enqueue). The worker then
trusts `job.duration_seconds`. `prober` is injected via a module-level `get_media_prober()` so
upload tests never call real ffprobe.

#### HTMX / response-targets contract for reject

The form (`app/templates/index.html`) already uses `hx-ext="response-targets"` with
`hx-target-429="#upload-error"`; fragments render into `<div id="upload-error">`. M009 extends it:

- Add `hx-target-400="#upload-error"` and `hx-target-422="#upload-error"` to the form.
- New partial `app/templates/partials/upload_error_message.html` with a `{{ message }}` variable
  (shared by 400 and 422; styled like the existing `upload_error.html`). The existing 429 partial is
  untouched.
- A reject helper (mirroring `_429_response`): HX request → `TemplateResponse` of the partial with
  status 400/422 into `#upload-error`; non-HX → `JSONResponse` (`{"detail": "..."}`) with the same
  status. Both `set_session_cookie`, delete the file, create no job.

### 6. Config — `app/config.py`

| Setting | Default | Env | Purpose |
|---|---|---|---|
| `max_duration_seconds` | `3600` | `MAX_DURATION_SECONDS` | 60-minute upload limit (strict `>`) |
| `media_backend` | `"ffmpeg"` | `MEDIA_BACKEND` | `ffmpeg` (real) / `fake` (CI/tests) |

### 7. Worker wiring — `app/workers/process_job.py`

Call `run_media_pipeline` with the backend from `get_media_backend()`; keep the existing guarded
transitions and staging cleanup unchanged.

### 8. Dockerfile — add ffmpeg

`RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*`
(ffmpeg pulls ffprobe; the shared image gives both `app` and `worker` the binaries).

### 9. CI workflow

Set `MEDIA_BACKEND=fake` in the test env (alongside the existing mock-transcription setting), so CI
stays binary-free.

### 10. Tests — see Testing Requirements.

### 11. `AI_WORKLOG.md` — milestone entry.

### 12. Update agent card — `docs/agents/media-pipeline-agent.md`

The card documents ffmpeg/ffprobe usage but predates a real wiring. Update it to state that the
screenshot **producer** is now real (single-pass `fps=1/20`, reconciled to `screenshot_timestamps`),
that duration is enforced at upload via ffprobe, and that the backend is selected by `MEDIA_BACKEND`
(`ffmpeg` | `fake`). Audio extraction / faster-whisper remain M010.

---

## Files expected to change during implementation

```
Dockerfile                                       # Modified: install ffmpeg (app+worker share the image)
app/config.py                                    # Modified: max_duration_seconds, media_backend
app/pipeline/media.py                            # New: protocols, real+fake impls, MediaError, get_media_backend
app/pipeline/media_pipeline.py                   # New: run_media_pipeline (or generalised from mock_pipeline)
app/pipeline/zip_packaging.py                    # Modified: build_zip(..., screenshots_dir=None)
app/pipeline/output_assembly.py                  # Modified: manifest from shared screenshot_timestamps (+ helper)
app/api/jobs.py                                  # Modified: upload-time ffprobe, 400/422 reject helper, duration_seconds
app/templates/index.html                         # Modified: hx-target-400/hx-target-422 -> #upload-error
app/templates/partials/upload_error_message.html # New: shared 400/422 inline fragment ({{ message }})
app/workers/process_job.py                       # Modified: call run_media_pipeline with selected backend
.github/workflows/*.yml                          # Modified: MEDIA_BACKEND=fake in test env
docs/agents/media-pipeline-agent.md              # Modified: real producer + backend selection note
tests/unit/test_media.py                         # New: screenshot_timestamps, fake impls, MediaError
tests/integration/test_media_pipeline.py         # New: pipeline invariant with fakes, real frame bytes
tests/test_upload_duration.py                    # New: upload reject 422/400, boundary 3600, HX/JSON, file deleted
tests/integration/test_media_smoke.py            # New: guarded real-binary ffmpeg/ffprobe smoke (skipif)
AI_WORKLOG.md                                    # Milestone entry
```

No new entries in `requirements.txt` — ffmpeg/ffprobe are system binaries, called via subprocess
(ADR 001).

---

## Out of Scope

- Real faster-whisper transcription (M010).
- Real audio extraction `ffmpeg -vn` (M010) — D2.
- Smart / scene-change screenshot detection (MVP3).
- Chunked transcription / long-video splitting (`chunk_count` stays `1`).
- Changing ZIP layout, manifest schema, timecode formula, or the `0.6` threshold.
- Changes to download / status / cleanup / session logic.
- New DB migration (`duration_seconds` already exists, nullable).
- Retry logic, deploy hardening (M011).
- Skills / MCP.

---

## Current state before M009

- `app/api/jobs.py` validates content-type / extension / magic bytes / size; **never sets
  `duration_seconds`** and never enforces the 60-minute limit.
- `app/models/job.py` already has nullable `duration_seconds` (Float) — no schema change needed.
- The worker success path calls `run_mock_output_pipeline(job)`; screenshots are placeholder JPEGs
  written inside `build_zip` from `screenshot_count`.
- `build_screenshots_manifest_csv` derives span from `duration_seconds` (always `None` today) or the
  transcript max-end (75 s for the mock fixture) → 4 placeholder screenshots.
- The upload form uses `hx-ext="response-targets"` + `hx-target-429="#upload-error"`.
- `Dockerfile` is `python:3.12-slim` with **no ffmpeg**; `app` and `worker` build from the same image.
- All M001–M008 tests green.

---

## Safe subprocess policy

- `subprocess.run([...], shell=False)` with an explicit argument list only. No f-string commands,
  never `shell=True`.
- Input path = trusted **validated resolved path under `upload_dir`** (same `allowed_dir` check as
  `app/services/cleanup.py:safe_delete`). Never `original_filename`, never any user string in argv.
- `capture_output=True`; raw stderr/tracebacks never reach the user. Map to safe codes:
  `invalid_or_unreadable_media`, `screenshot_extraction_failed`.
- Timeouts on every call (ffprobe ~30 s; ffmpeg covered by `rq_job_timeout` plus its own cap).
- Strict numeric parsing of ffprobe output (float; reject empty/NaN/inf/≤ 0).
- **`--` (end-of-options) is NOT mandatory** — ffmpeg/ffprobe do not guarantee POSIX `--` semantics.
  Use `--` only if a Docker smoke confirms the specific command accepts it; otherwise argv safety
  comes from the trusted UUID-named path under `upload_dir`.

---

## Testing requirements

All fake-seam tests are deterministic, fast, no network, **no ffmpeg** (`MEDIA_BACKEND=fake`).
Target `pytest tests/ -v` under 60 s.

### Fake-seam (CI, no ffmpeg)

`tests/unit/test_media.py`:
- `screenshot_timestamps(span)` (`0,20,40… while t < span`, span > 0):
  75 → `[0,20,40,60]`; 3600 → 180 entries (`0..3580`); 20 → `[0]`; 0 → `[]`.
- **Short video < 20 s**: 19 → `[0]`; 15 → `[0]`; 5 → `[0]` — exactly one screenshot at 0 s.
- `FakeScreenshotExtractor` creates exactly the requested `frame_NNNNNNs.jpg` files with valid
  JPEG bytes; `FakeMediaProber` returns the configured duration; `MediaError` surfaces safe codes.

`tests/test_upload_duration.py` (fake prober via `get_media_prober`):
- `3601` → 422, file deleted, no job row, no enqueue.
- `3600` exactly → allowed, job created with `duration_seconds=3600`.
- `1800` → job created with `duration_seconds=1800`.
- `MediaError` → 400, file deleted, no job.
- **HTMX reject**: `HX-Request: true` + duration > 60 min → inline fragment in `#upload-error`
  (status 422); same without HX → JSON 422. Both: file deleted, no job. Analogous for 400.

`tests/integration/test_media_pipeline.py` (fakes):
- Invariant `metadata.screenshot_count == manifest rows == files in screenshots/`.
- ZIP screenshot entries contain the real (non-empty) frame bytes produced by the extractor.
- `build_zip(screenshots_dir=...)` adds real files, correct names, invariant holds.
- M004 `build_zip` without `screenshots_dir` still passes (placeholder path intact).
- Worker success/failure transitions and staging cleanup unchanged with the new pipeline.

### Real-binary smoke (guarded)

`tests/integration/test_media_smoke.py`:
- `@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, ...)`.
- Generate a tiny synthetic video with ffmpeg `testsrc` (~65 s, low-res/low-fps, small) into tmp.
- Real ffprobe duration ≈ 65 s (within tolerance).
- Real extractor → frames `[0,20,40,60]`, count 4, non-empty valid JPEGs.
- **Auto-skips in minimal CI** (no ffmpeg) → honours "CI must not require heavy video processing";
  runs in the Docker/manual gate and locally where ffmpeg is present.

---

## Security review focus (security-agent, clean context)

Map to `security-agent.md` §2 (path traversal) and §3 (subprocess injection):

1. **Subprocess** — list args, `shell=False`, timeouts, `capture_output`. Grep for `shell=True` → 0.
   `--` not required (only if Docker smoke confirms).
2. **No user string in argv** — `original_filename` and any request value never reach ffmpeg/ffprobe;
   frame filenames derive only from integer seconds.
3. **Path validation** — `job.input_path` resolves under `upload_dir` before any media call; no
   traversal into staging/output.
4. **Upload reject cleanup** — 400/422 deletes the saved file → no orphan uploads on rejection.
5. **ffprobe output parsed safely** — numeric only, no `eval`/`exec`; reject empty/NaN/inf/≤ 0.
6. **No leakage** — `error_message` and output files contain no raw tracebacks, host paths, or secrets.
7. **ZIP entry paths** remain fixed strings; frame names from integer seconds only.

Provide the clean Security/Reviewer prompt with **diff + acceptance criteria only** (no state files).

---

## Docker / manual gate

`Dockerfile` changes → **rebuild the image**:

```powershell
docker compose run --rm app python -m alembic upgrade head
docker compose up -d --build app worker

Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json
# Expected: status=ok, db=ok, redis=ok
docker compose run --rm app ffprobe -version
```

Tiny synthetic fixtures (no huge files):

```powershell
# short valid video (~65 s) for the happy path:
ffmpeg -f lavfi -i testsrc=size=128x72:rate=1:duration=65 -pix_fmt yuv420p short.mp4

# >60 min for the reject case — ULTRA-low-fps (~61 frames instead of 3601): fast to make, tiny file:
ffmpeg -f lavfi -i testsrc=size=64x64:rate=1/60:duration=3601 -pix_fmt yuv420p toolong.mp4
```

> Ultra-low-fps changes the frame count, not the container duration. Before the smoke, confirm the
> fixture really is > 60 min:
> ```powershell
> ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 toolong.mp4
> # expected value > 3600
> ```
> If a particular ffmpeg build reports duration differently, adjust `duration=`/`rate=` until ffprobe
> returns > 3600. The gate is valid only with an ffprobe-confirmed > 3600 fixture.

Manual smoke:
- Upload `short.mp4` (< 60 min) → poll to `done` → the ZIP has **real** (non-placeholder) JPEGs,
  frame count == manifest rows == `metadata.screenshot_count`, and `duration_seconds` is real.
- Upload `toolong.mp4` (ffprobe-confirmed > 60 min) → reject at upload (422) with a clear error,
  no job created, file deleted.

Record outcomes in `AI_WORKLOG.md`.

---

## Acceptance criteria

**AC1** — ffprobe determines `duration_seconds`, persisted on the job at upload.

**AC2** — Video > 60 min is rejected **at upload** (422) before enqueue; the file is deleted and no
job is created. A video of exactly 3600 s (60 min) is allowed (strict `>`).

**AC3** — An unreadable/corrupt media file (that passed magic-bytes) → 400, file deleted, no job.

**AC4** — Real screenshots are extracted every 20 s via single-pass ffmpeg, named `frame_NNNNNNs.jpg`.

**AC5** — The invariant `metadata.screenshot_count == manifest rows == files in screenshots/` holds,
driven by the shared `screenshot_timestamps`.

**AC6** — The ZIP screenshots are real JPEG frames from the video (not placeholders);
`metadata.duration_seconds` is the real value.

**AC7** — ZIP layout, manifest schema, `frame_NNNNNNs.jpg`, the timecode formula, and the `0.6`
threshold are unchanged.

**AC8** — `MediaProber` / `ScreenshotExtractor` are injectable; `MEDIA_BACKEND=fake` is used in
CI/tests.

**AC9** — Every subprocess uses list args, `shell=False`, timeouts, `capture_output`, and a trusted
validated path under `upload_dir`; no user string in argv; `shell=True` is absent. `--` is not
required (apply only if Docker smoke confirms support).

**AC10** — CI is green without ffmpeg (fake seam); the real-binary smoke auto-skips when binaries are
absent.

**AC11** — `Dockerfile` installs ffmpeg; the canonical gate (`up -d --build`) and `ffprobe -version`
pass.

**AC12** — Existing M001–M008 tests stay green; M004 `build_zip` tests are not broken.

**AC13** — No new DB migration is introduced.

**AC14** — Real transcription / audio extraction are NOT introduced (they remain M010).

**AC15** — Staging cleanup runs on success and failure; per-job staging directories do not accumulate.

**AC16** — A short video < 20 s yields exactly one screenshot at 0 s; manifest / metadata / files stay
consistent.

**AC17** — An HTMX duration-reject renders an inline error in `#upload-error` (422); a non-HX request
gets JSON 422; 400 behaves analogously. In all cases the file is deleted and no job is created.

**AC18** — Security review by `security-agent` completed in clean context (diff + ACs); no
High/Critical open findings.

**AC19** — Docker manual gate executed and recorded in `AI_WORKLOG.md` (happy path real screenshots +
real duration; > 60 min reject with ffprobe-confirmed fixture).

---

## Quality gates

| Gate | Required | Notes |
|---|---|---|
| Functional (manual) | Always | Real screenshots + duration reject via the Docker gate |
| Tests (pytest green) | Always | Fake-seam unit/integration + M001–M008 regression |
| Security review | Always | `security-agent`, clean context; subprocess + path handling are critical |
| Human understanding | Always | Reviewer can explain the `screenshot_timestamps` single-source-of-truth invariant and the upload-time enforcement choice |
| Performance | Contextual | Single-pass `fps=1/20` keeps extraction to one subprocess; ffprobe is metadata-only |
| Reliability | Contextual | ffmpeg/ffprobe failures map to safe `error_message`; staging cleanup on success and failure |
| DB migration | N/A | `duration_seconds` already exists; no migration |
| Documentation | Always | ADR 005 + this spec + `media-pipeline-agent.md` (producer + enforcement change) |

---

## Implementation order

1. ADR 005 + this spec (docs-only branch) — **current session**.
2. `screenshot_timestamps` helper + refactor `build_screenshots_manifest_csv` onto it (+ unit tests).
3. `app/pipeline/media.py`: protocols, fake impls, `MediaError`, `get_media_backend` (+ unit tests on fakes).
4. Real ffprobe/ffmpeg impls + guarded real-binary smoke test.
5. `build_zip(screenshots_dir=...)` (+ tests; M004 tests stay green).
6. `run_media_pipeline` + worker wiring (+ pipeline tests on fakes; invariant).
7. Upload-time ffprobe + 400/422 reject helper + `upload_error_message.html` partial +
   `hx-target-400/422` on the form + `duration_seconds` (+ upload tests on the fake prober:
   HX inline / non-HX JSON / boundary 3600 / short video).
8. `Dockerfile` ffmpeg; CI `MEDIA_BACKEND=fake`.
9. `pytest tests/ -v`; canonical Docker gate (`up -d --build`) + manual smoke; record in `AI_WORKLOG.md`.
10. `security-agent` + Codex Reviewer in clean contexts (diff + ACs only).
11. Human sign-off → merge. (Max 2 reviewer-fix cycles per CLAUDE.md.)

---

## Open questions / deferred risks

1. **Mock transcript vs real duration.** In M009 the transcript stays the mock 0–75 s fixture while
   screenshots span the real duration. Intentional and documented; resolved by M010 (real transcription).
2. **ffmpeg `fps=1/20` frame→second mapping** must be confirmed by the real-binary smoke; the
   single-source `screenshot_timestamps` + reconciliation keeps the invariant even if ffmpeg emits an
   extra trailing frame.
3. **ffprobe in the request path** adds sub-second latency on accepted uploads (metadata-only read) —
   accepted (ADR 005).
4. **CI has no ffmpeg.** Real subprocess wiring is covered by the guarded smoke (Docker/manual gate),
   not by minimal CI — the fake seam keeps CI green.
5. **Audio extraction / faster-whisper** stay M010; do not pull them into M009 (one branch = one feature).
