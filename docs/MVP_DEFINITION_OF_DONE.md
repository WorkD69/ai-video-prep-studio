# MVP Definition of Done

## The Single Test That Matters

> A fresh user runs `docker compose up --build`, uploads a video, downloads a ZIP,
> uploads that ZIP to ChatGPT or Claude, and gets useful analysis.
> No manual steps. No intervention. Works end-to-end.

---

## Acceptance Checklist

### Infrastructure
- [ ] `docker compose up --build` succeeds on a clean machine with no pre-existing data
- [ ] All services start: `app` (FastAPI), `worker` (RQ), `redis`, `postgres`
- [ ] Services are healthy (healthchecks pass)
- [ ] No hardcoded secrets — all config from environment variables

### Upload
- [ ] User can upload a video file via the web UI
- [ ] File size limit enforced: files > 500 MB are rejected with a clear error
- [ ] Duration limit enforced: videos > 60 minutes are rejected with a clear error
- [ ] File type validated by content (not just extension)
- [ ] Only 1 active job per session/IP is allowed; second upload is blocked with explanation

### Processing
- [ ] Job is queued and picked up by the RQ worker
- [ ] Status page shows real-time progress (queued → processing → done / failed)
- [ ] Processing completes without errors for a typical lecture video (MP4, 30-60 min)
- [ ] Temp files are cleaned up after processing (uploads/ directory does not grow)

### ZIP Output
- [ ] ZIP file is produced at `outputs/llm_analysis_package_<stem>_<YYYYMMDD_HHMMSS>.zip`
- [ ] ZIP contains all required files:
  - [ ] `transcript_full_global_timecodes.md`
  - [ ] `transcript_full_global_timecodes.json`
  - [ ] `lecture_summary_input.md`
  - [ ] `screenshots_manifest.csv`
  - [ ] `failed_or_silent_parts.md`
  - [ ] `metadata.json`
  - [ ] `screenshots/frame_NNNNNNs.jpg` (one per 20 seconds)
- [ ] Global timecodes are correct (chunk_offset + local)
- [ ] Silent/failed segments appear in `failed_or_silent_parts.md` with correct timecodes
- [ ] `lecture_summary_input.md` has English section headers and original-language transcript
- [ ] `screenshots_manifest.csv` has a row for every screenshot file

### Download
- [ ] User can download the ZIP from the status page
- [ ] ZIP is available for 24 hours after creation
- [ ] After 24 hours, ZIP is deleted and download link returns 410 Gone

### LLM Usability (Manual Verification)
- [ ] ZIP can be attached to a ChatGPT or Claude conversation
- [ ] The transcript is readable and properly timestamped
- [ ] The lecture summary input document provides useful context
- [ ] A non-technical reviewer can understand what the video is about from the ZIP alone

### Tests
- [ ] `pytest` passes with 0 failures (using MockTranscriber in CI)
- [ ] Tests cover: global timecode conversion, silent parts detection, ZIP contents, manifest CSV
- [ ] CI pipeline (GitHub Actions) passes on `main`

### Security Baseline
- [ ] No path traversal possible via filename manipulation
- [ ] No shell injection possible via ffmpeg commands
- [ ] Uploaded files are stored with safe generated names, not original filenames
- [ ] No secrets in source code or committed `.env` files

---

## Not Required for MVP Done

- User accounts
- Email notifications
- Admin panel
- Metrics / monitoring
- HTTPS (local docker deploy is HTTP)
- Smart slide detection
- Speaker diarization
