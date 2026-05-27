# Roadmap

## MVP 1 — End-to-End (Current Target)

**Goal:** Working product. Upload video → download ZIP → use in ChatGPT/Claude.

**Scope:**
- File upload (500 MB max, 60 min max)
- Async processing via RQ
- Full transcript with global timecodes (MD + JSON)
- Screenshots every 20 seconds
- Failed/silent parts detection (no_speech_prob ≥ 0.6)
- lecture_summary_input.md (English structure, original-language transcript)
- ZIP download
- Job status page (polling via HTMX)
- 24h file retention + cleanup
- 1 active job per session/IP
- Docker deploy (`docker compose up --build`)
- GitHub Actions CI (pytest with MockTranscriber)

**Not in MVP 1:**
- User accounts / auth
- Payments
- Email notifications
- Smart slide detection
- S3 / cloud storage
- React / SPA frontend
- Multiple concurrent jobs
- Horizontal scaling

**Done when:** A fresh user runs `docker compose up --build`, uploads a video, downloads the ZIP, and can feed that ZIP directly to ChatGPT or Claude for analysis. Works end-to-end without manual intervention.

---

## MVP 2 — Polish & Observability

**Goal:** Production-ready for small user base.

**Scope (tentative):**
- Job history page (last N jobs per session)
- Email notification on job completion
- Basic metrics (Prometheus + Grafana or simpler)
- Error reporting (Sentry or similar)
- Retry logic for failed jobs
- Better progress indication (real-time via SSE)
- Admin panel (view jobs, trigger cleanup)
- Rate limiting hardening
- HTTPS / reverse proxy (nginx) in compose
- `.env.example` + deployment guide

**Not in MVP 2:**
- User accounts
- Payments
- Smart slide detection
- S3

---

## MVP 3 — Intelligence & Scale

**Goal:** Smarter output, broader deployment options.

**Scope (tentative):**
- Smart slide detection (scene change analysis, not just fixed interval)
- Speaker diarization (faster-whisper + pyannote.audio)
- Chapter detection from transcript
- User accounts (optional login, job history)
- S3 / object storage backend
- Horizontal worker scaling
- Webhook notifications

**Not in MVP 3:**
- Payments / billing
- Mobile app
- Real-time collaborative editing

---

## Future Commercial Phase

**Goal:** Sustainable product with paying users.

Commercialization is not part of MVP 1–3, but remains a future product direction.

**Scope (tentative):**
- User accounts (registration, login, persistent job history)
- Paid tiers (free minutes/month, paid top-up or subscription)
- Usage limits enforced by minutes of processed video
- Billing provider integration (Stripe or equivalent)
- Per-user job history and re-download
- S3 / object storage backend (larger files, longer retention)
- Increased file size and duration limits for paid tiers
- Batch processing (multiple videos per job)
- React / Next.js frontend if UI complexity requires it

**Not in this phase:**
- Mobile applications
- Real-time video streaming
- Video editing features

---

## Notes on Deferred Technologies

**React / Next.js:** Not needed for MVP 1–3 (Jinja2 + HTMX is sufficient). Will be reconsidered when UI complexity justifies the build toolchain overhead.

**Mobile app:** No plan currently. Not forbidden — deferred indefinitely until product-market fit is established.
