# ADR 001 — Stack Choice

**Status:** Accepted  
**Date:** 2026-05-27  
**Deciders:** Project founder (Artem)

---

## Context

We need to build a web service that:
- Accepts video file uploads (up to 500 MB)
- Processes them asynchronously (transcription + ffmpeg can take 5–30 minutes)
- Serves a simple status/download UI
- Deploys as a single `docker compose up` command
- Supports CI with mocked heavy dependencies (faster-whisper)

The team is a solo developer with Python backend experience. The product is a focused utility tool, not a general platform. We want to ship MVP fast, not build for hypothetical scale.

---

## Decision

### Backend: FastAPI

**Rationale:**
- Modern async Python, excellent performance for I/O-bound upload/download endpoints
- Native Pydantic integration for request validation
- Auto-generated OpenAPI docs (useful during development)
- Strong ecosystem, well-documented
- Jinja2 templating built-in for server-rendered HTML

**Rejected alternatives:**
- Django: heavier, ORM coupling, more boilerplate for an API-first service
- Flask: less built-in (no async, no validation), more assembly required

### Database: PostgreSQL + SQLAlchemy 2.x + Alembic

**Rationale:**
- PostgreSQL is the reliable default for structured data (jobs, usage logs)
- SQLAlchemy 2.x provides clean async ORM with type safety
- Alembic for schema migrations from day one (no manual SQL migrations)
- Well-understood stack, widely deployed

**Rejected alternatives:**
- SQLite: fine for dev, problematic with concurrent writes from multiple containers
- MongoDB: no benefit for our simple relational data model

### Frontend: Jinja2 + HTMX + TailwindCSS (CDN)

**Rationale:**
- No build step, no Node.js dependency, no SPA complexity
- HTMX provides reactive UI (status polling, form submissions) without JavaScript fatigue
- TailwindCSS from CDN: zero setup, good enough for a utility tool UI
- Server-rendered HTML: simpler mental model, easier debugging

**Rejected alternatives:**
- React / Next.js: massive overkill for upload form + status page + download button
- Vue / Svelte: still requires build toolchain and separate concerns

### Logging: structlog

**Rationale:**
- Structured JSON logs from day one
- Mandatory `job_id` + `stage` fields on every log line for job traceability
- Easy to add context (bound loggers)
- No performance penalty vs stdlib logging

### CI: GitHub Actions

**Rationale:**
- Integrated with GitHub (where code lives)
- Free for public repos, affordable for private
- Uses `MockTranscriber` in CI to avoid faster-whisper model download in pipeline

### Deploy: Docker + docker-compose

**Rationale:**
- Single command deployment: `docker compose up --build`
- Reproducible environment (matches production)
- Standard for small self-hosted services
- Avoids "works on my machine" problems

---

## Consequences

**Positive:**
- Fast to bootstrap, easy to reason about
- All components are well-documented and widely used
- No exotic dependencies
- Single deploy command lowers operational friction

**Negative / Accepted Trade-offs:**
- Jinja2 + HTMX limits frontend interactivity (acceptable for MVP utility tool)
- Single-node PostgreSQL has no built-in HA (acceptable for MVP)
- No S3 means file retention requires a cleanup cron job

**Future migration paths (not in MVP):**
- Frontend → React if complex UI is needed (MVP3+)
- Storage → S3-compatible if local disk becomes a constraint (MVP3+)
- PostgreSQL → managed RDS if operational complexity grows
