# AI Handoff - AI Video Prep Studio

Current session handoff. Overwrite this file before closing a session; do not append old
history.

---

## Last Updated

2026-10-25

## Role / Process Context

This session implemented the whole ADR 006 pivot in **light mode** (Artem asked to execute the
approved plan end to end; plan copy: `tmp/plans/crv-pivot-plan.md`). Respond to Artem in Russian.

## What This Session Completed

- Evaluated open-source alternatives; audited claude-real-video (crv) commit `ab4b9b2b…` (no
  malware/telemetry/install-time code) and tested it on a real 51-min video.
- ADR 006 + CLAUDE.md/ROADMAP (PR #24). M009 merged (PR #23).
- Implementation (stacked branches, one per stage): GPU worker, faster-whisper transcriber + VTT
  parser + audio extraction, crv frame selector with ffmpeg fallback, URL intake (yt-dlp API), LLM
  package v2, Russian UI with stages, security-review fixes, fake-IP proxy setting.
- Docker E2E on the real stack: URL job (manual subtitles, 31 chapters, 256 frames) 7 min; file
  job (whisper turbo on GPU, hotwords fixed term spelling) 12 min; negative checks (foreign Origin,
  localhost/compose/file:// links, foreign Host, Sec-Fetch-Site) all rejected.

## Machine-specific facts (Artem's PC)

- `.env` (gitignored): `DATA_DIR=A:/aivps-data`, `HF_CACHE_DIR=A:/AI/hf-cache`,
  `URL_EXTRA_ALLOWED_NETWORKS=["198.18.0.0/15"]` — a fake-IP VPN/proxy makes every site resolve to
  198.18.x.x inside Docker.
- A native Windows PostgreSQL service listens on host port 5433 (not this project).
- Docker Desktop disk (`docker_data.vhdx`, ~43 GB) is on C:, which has ≈5 GB free.
- Another agent uses Docker Desktop in parallel (hackathon): never restart Docker / `wsl --shutdown`
  / prune caches without asking.

## Recommended Next Action

Artem checks the two ZIPs in `A:\aivps-data\outputs` (deleted after 24 h) by giving one to an
agent. Optional follow-ups are listed in `PROJECT_STATE.md`.
