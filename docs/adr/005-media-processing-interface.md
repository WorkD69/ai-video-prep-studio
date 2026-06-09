# ADR 005 — Media Processing Interface (ffprobe/ffmpeg) + Duration Enforcement Point

**Status:** Accepted
**Date:** 2026-06-09
**Deciders:** Project founder (Artem)
**Relates to:** ADR 003 (Transcriber interface — this ADR mirrors its swappable-backend pattern)

---

## Context

`CLAUDE.md` sets two MVP hard limits that depend on real media processing:

- **Max video duration: 60 minutes** — must reject longer videos with a clear error.
- **Screenshot interval: 20 seconds** — the ZIP package must contain one real frame every 20 s.

Today neither is real:

- `app/pipeline/upload.py` validates content-type, extension, magic bytes, and size, but
  **`duration_seconds` is never set anywhere** and the 60-minute limit is **not enforced**.
- M004 produces **placeholder** screenshots (`_PLACEHOLDER_JPEG` written inside
  `app/pipeline/zip_packaging.py`); no frames are extracted from the uploaded video.

M004 explicitly designed the ZIP/output contract so that "later ffmpeg implementation can replace
only the producer, not the ZIP/output contract". This ADR decides **how** ffprobe/ffmpeg are wired
in: the interface shape, the subprocess-safety rules, and — the one genuine architectural choice —
**where the duration limit is enforced**.

Constraints carried from `CLAUDE.md`:

- CI must stay green **without** heavy media binaries (ADR 001: CI uses mock transcription).
- Subprocess must be injection-safe (no shell, no user strings in argv).
- faster-whisper transcription is a **separate** later milestone (M010); this ADR does not introduce it.

---

## Decision

### 1. Media processing behind swappable interfaces (mirror of ADR 003)

```python
class MediaProber(Protocol):
    def probe_duration(self, input_path: str) -> float: ...

class ScreenshotExtractor(Protocol):
    def extract(self, input_path: str, out_dir: Path, timestamps: list[int]) -> list[Path]: ...
```

- **Real** implementations (`FfprobeMediaProber`, `FfmpegScreenshotExtractor`) shell out to
  `ffprobe` / `ffmpeg`.
- **Fake** implementations (`FakeMediaProber`, `FakeScreenshotExtractor`) return deterministic data
  and write placeholder JPEG bytes — no binaries, used in CI/tests.
- Selection by env: **`MEDIA_BACKEND`** (`ffmpeg` | `fake`), exactly like `TRANSCRIBER=mock` in
  ADR 003. CI/tests run `MEDIA_BACKEND=fake`.

This keeps the pipeline logic decoupled from the binaries and keeps CI binary-free, the same trade
already accepted for transcription.

### 2. Subprocess safety

- Only `subprocess.run([...], shell=False)` with an explicit argument list. Never `shell=True`,
  never an f-string command.
- The input path is always a **trusted, validated, resolved path under `upload_dir`** (the same
  `allowed_dir` check used by `app/services/cleanup.py:safe_delete`). No `original_filename` and no
  user-controlled string ever enters argv.
- `capture_output=True`; raw stderr/tracebacks never reach the user. Failures map to safe codes
  (`invalid_or_unreadable_media`, `screenshot_extraction_failed`).
- Timeouts on every call; strict numeric parsing of ffprobe output (reject empty/NaN/inf/≤ 0).
- **`--` (end-of-options) is NOT mandatory.** ffmpeg/ffprobe do not guarantee POSIX `--` semantics;
  argv safety comes from the trusted UUID-named path under `upload_dir`, not from `--`. Use `--`
  only if a Docker smoke confirms the specific command accepts it.

### 3. Duration enforced synchronously at upload

ffprobe runs in the **upload request path**, right after the file is saved and **before** the job
is enqueued:

- `duration > 3600` → reject (**HTTP 422**), delete the saved file, do not create a job.
- exactly `3600` (60 min) → allowed (strict `>`).
- unreadable/corrupt media (passed magic-bytes but ffprobe fails) → reject (**HTTP 400**), delete file.
- success → persist `duration_seconds` on the job.

**Why upload-time, not worker-time:** best UX (rejection before queueing, not after a wait) and it
matches where `MVP_DEFINITION_OF_DONE.md` places the limit (the **Upload** section). The cost is
ffmpeg/ffprobe in the `app` image as well as the `worker` image — a single shared-image Dockerfile
change. ffprobe reads container metadata only (no decode), so the added request latency is sub-second.

### 4. Screenshots: single-pass extraction reconciled to the manifest

- One `ffmpeg -vf fps=1/20` pass extracts all frames; output indices are renamed to
  `frame_NNNNNNs.jpg` where `second = (index − 1) × 20`.
- A single helper `screenshot_timestamps(span) = 0, 20, 40, … while t < span` is the **single source
  of truth** for both the manifest rows and the requested extractor timestamps, so the invariant
  `metadata.screenshot_count == manifest rows == files in screenshots/` holds **by construction**.
- The ZIP layout, manifest schema, `frame_NNNNNNs.jpg` naming, global-timecode formula, and the
  `0.6` silence threshold are **unchanged**. Only the screenshot **producer** changes.

faster-whisper / real audio extraction are **not** part of this ADR (M010). M009 keeps
`MockTranscriber` on a placeholder audio file.

---

## Rationale

**1. Symmetry with the accepted Transcriber pattern.** ADR 003 already established
"real impl + deterministic fake behind a Protocol, selected by env, fake in CI". Reusing that mental
model for ffprobe/ffmpeg minimises new concepts and keeps CI binary-free.

**2. Invariant by construction, not by coincidence.** Deriving manifest rows and extracted frames
from one `screenshot_timestamps` list removes the class of bug where frame count and manifest count
drift apart.

**3. Honest about subprocess safety.** The path into ffmpeg is a server-minted UUID filename under
`upload_dir`; the user-supplied `original_filename` is only ever used for display. This satisfies
`security-agent.md` §3 (no `shell=True`, no user-controlled values in argv) without relying on `--`,
which ffmpeg/ffprobe do not treat as a portable end-of-options marker.

**Rejected alternatives:**

- **Enforce duration in the worker** — simpler deployment story (ffmpeg conceptually worker-only),
  but worse UX: the user uploads a 500 MB file, waits in the queue, and only then learns it was
  rejected. Rejected; the limit belongs at the door (DoD Upload section).
- **Per-timestamp `ffmpeg -ss T -frames:v 1` seeking** — exact frames, but up to ~180 subprocess
  spawns for a 60-minute video. Rejected for the single-pass `fps=1/20` (one spawn) with
  manifest reconciliation.
- **Real audio extraction / faster-whisper now** — would blow up M009 scope (model downloads, CI
  complexity) and violate "one branch = one feature". Deferred to M010.
- **A python ffmpeg wrapper library (`ffmpeg-python` etc.)** — ADR 001 locks "ffmpeg + ffprobe via
  subprocess"; adding a wrapper dependency buys nothing for two fixed command shapes. Rejected.

---

## Consequences

**Positive:**

- 60-minute limit becomes enforceable; `duration_seconds` becomes real (feeds `metadata.json`).
- Real screenshots replace placeholders with **zero change** to the ZIP/manifest contract.
- CI stays green with no ffmpeg (fake seam); one guarded real-binary smoke test covers the wiring.
- Establishes the safe-subprocess foundation that M010 (faster-whisper audio extraction) will reuse.

**Negative / accepted trade-offs:**

- ffmpeg added to the shared Docker image (app **and** worker) → larger image. Accepted.
- ffprobe runs in the upload request path → sub-second added latency on accepted uploads. Accepted
  (metadata-only read).
- M009 transcript stays mock (0–75 s) while screenshots span the real duration — an intentional,
  documented inconsistency until M010 brings real transcription.

**Migration path:**

- M010: real audio extraction (`ffmpeg -vn …`) + `FasterWhisperTranscriber`, selected by the
  existing transcriber seam (ADR 003), reusing this ADR's subprocess policy.
- M011: deploy hardening / clean E2E smoke over the now-real pipeline.

---

## Security notes (for security-agent)

- All subprocess calls: list args, `shell=False`, timeouts, `capture_output=True`; **grep for
  `shell=True` must be empty**.
- Input path validated under `upload_dir` (resolve + `is_relative_to`) before any ffmpeg/ffprobe call;
  no path traversal into staging/output.
- No user-controlled string (e.g. `original_filename`) in argv; frame filenames derive only from
  integer seconds.
- ffprobe output parsed numerically (no `eval`/`exec`); reject empty/NaN/inf/≤ 0.
- Upload reject (400/422) deletes the saved file → no orphan uploads on rejection.
- Safe `error_message` only — no raw tracebacks, host paths, or secrets in user-visible output.
