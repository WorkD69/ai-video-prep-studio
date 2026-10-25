# ADR 006 — crv Frame Engine, Real Transcription, URL Intake, LLM Package v2

**Status:** Accepted
**Date:** 2026-10-25
**Deciders:** Project founder (Artem)
**Supersedes (in part):** ADR 005 §"screenshots every 20 s" (kept only as fallback); CLAUDE.md MVP limits
**Relates to:** ADR 003 (Transcriber interface), ADR 005 (media interfaces, subprocess safety)

---

## Context

After M009 the service has the full shell (upload, RQ, status page, ZIP download, retention, session
limit, ffprobe/ffmpeg) but is not usable: transcription is a mock, there is no URL input, and
screenshots are taken every 20 s with no link to the transcript text.

The product is a **local, single-user tool**: drop a video file or a link, get a ZIP an LLM agent can
"watch" — transcript with timecodes, frames placed inside the text, chapters, metadata.

We evaluated open-source tools (2026-10-25). Nothing produces the target package end-to-end. The
closest, [claude-real-video](https://github.com/HUANGCHIHHUNGLeo/claude-real-video) (crv, MIT), has a
good frame selector (ffmpeg scene-change + density floor + perceptual dedup). A security audit of
commit `ab4b9b2b23790f5d63a6d15165d3d8ba284c6023` found no malicious code, no telemetry, no install-time
code, list-argv subprocess only. Test run on a 51-min Russian video:

- frame selection quality: good; 640 px default is unreadable for on-screen code, 1280 px is readable;
- one unconditional ffmpeg pass (`temporal_check`, slow-motion hint) costs ~1/3 of runtime;
- local files are copied into the output dir (2× disk);
- faster-whisper `turbo` on an RTX 3070: 51 min of audio in ~4 min, ~0.90 word similarity with the
  author's manual subtitles; English terms get transliterated ("Клод Код") without hints;
- PyAV ≥ 16 breaks faster-whisper 1.2.1 audio decoding (`metadata_errors`) — needs `av<16`.

---

## Decision

### 1. crv is the frame engine only

- Installed from the audited commit tarball (not PyPI), in `requirements-media.txt` (Docker only).
- Called as a library: `claude_real_video.process(video, out, do_transcribe=False, frame_width=1280,
  dedup_threshold=15, max_frames=N)` with `N = min(300, max(20, ceil(duration_min * 5)))`.
- Two monkeypatches, isolated in `app/pipeline/frames_crv.py` and guarded by a canary test:
  1. `claude_real_video.temporal_check.temporal_hint` → returns `None` (skip the hint pass);
  2. `claude_real_video.core.fetch_video` → returns the input path (no copy).
- Frames are renamed to the existing `frame_NNNNNNs.jpg` convention (whole seconds; on a same-second
  collision the first frame wins).
- **Fallback:** if crv fails, the M009 `FfmpegScreenshotExtractor` (every 20 s) runs instead;
  `metadata.json.frames_mode` records `scene_change` or `fixed_20s`.
- Behind a `FrameSelector` interface with a fake for CI (same pattern as ADR 003/005).

### 2. Real transcription: `FasterWhisperTranscriber`

- Implements the ADR 003 `Transcriber` protocol. Model `turbo`, `device="auto"`,
  `vad_filter=True`, `condition_on_previous_text=False`.
- Domain terms via `hotwords` (config glossary + per-job terms + video title).
- Selected by `TRANSCRIBER_BACKEND` (`faster_whisper` | `mock`); CI uses `mock`.
- GPU in Docker via the nvidia runtime; cuBLAS/cuDNN from pip wheels; `av<16` pinned.

### 3. Transcript source priority

1. **Manual subtitles** uploaded by the author in the video's language (URL jobs only) → parsed VTT.
2. Otherwise **whisper**. Auto-generated captions are never used.

### 4. URL intake

- `POST /jobs/from-url`; any site yt-dlp supports. yt-dlp is used through its **Python API**
  (no subprocess, no option parsing of user input).
- SSRF guard: http/https only; the host must resolve to public addresses only (no loopback, private,
  link-local, reserved). No playlists, no live streams.
- Probe at submit (duration limit enforced before enqueue, like ADR 005 §3); download in the worker
  (`height<=720`, mp4, `max_filesize`).

### 5. LLM package v2 (ZIP filename unchanged)

```
README.md            how the agent should read the package
transcript.md        chapters as headings; [hh:mm:ss–hh:mm:ss] segments; frames inline below the
                     segment whose time span contains them (frames in pauses → next segment)
transcript.json      segments with global timecodes
frames/frame_NNNNNNs.jpg
frames_manifest.csv  frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id
failed_parts.md      no-speech gaps > 30 s and segments with no_speech_prob >= 0.6
metadata.json        source, title, duration, language, transcript_source, frames_mode, chapters …
subtitles.<lang>.vtt only when manual subtitles were used
```

`lecture_summary_input.md` is removed (README.md + transcript.md replace it). The `screenshots/`
directory is renamed to `frames/`.

### 6. Limits and local hardening

- Max upload 5 GiB, max duration 3 h (strict `>`), RQ job timeout 4 h.
- All compose ports bound to `127.0.0.1`. State-changing POSTs reject a foreign `Origin`.
- Data directory configurable (`DATA_DIR`) so large files can live off the system drive.

### 7. Process

For this pivot the project runs in **light mode**: one ADR, one branch per stage, pytest green at each
stage, one clean security review at the end (upload / URL / subprocess / paths / CSRF).

---

## Consequences

- The Docker image grows by ~2–3 GB (CUDA libs, faster-whisper, crv). CI stays light (base
  requirements only, fakes/mocks).
- We depend on a young, commercially-backed project. Mitigation: pinned commit, patches isolated in
  one module, canary test, ffmpeg fallback.
- Two crv monkeypatches rely on private internals; a crv upgrade must re-run the audit and the canary.
- yt-dlp must be bumped when sites change; probe/download errors map to safe user messages.
