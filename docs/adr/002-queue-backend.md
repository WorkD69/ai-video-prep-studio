# ADR 002 — Queue Backend: RQ over Celery

**Status:** Accepted  
**Date:** 2026-05-27  
**Deciders:** Project founder (Artem)

---

## Context

Video processing jobs (ffmpeg + transcription) are long-running (5–30 min) and must run asynchronously — the HTTP request cannot block. We need a task queue to:
- Accept job submissions from FastAPI
- Execute jobs in a separate worker process
- Report job status back to the web UI
- Survive worker crashes without losing job metadata

Redis is already required in the stack for caching and session state. The question is which task queue library to use on top of it.

---

## Decision

**Use RQ (Redis Queue) instead of Celery.**

---

## Rationale

### RQ Advantages

**1. Zero extra infrastructure.**
RQ uses Redis directly. Redis is already in our stack (ADR 001). No separate message broker needed.

**2. Dramatically simpler configuration.**
Celery requires: broker URL, result backend URL, worker command, beat scheduler (if needed), serialization config, task routing config.  
RQ requires: Redis connection string. That's it.

**3. Simpler codebase.**
Celery tasks need decorators, `@app.task`, import-time registration, and care around circular imports.  
RQ jobs are plain Python functions. Pass the function reference to `queue.enqueue()`. Done.

**4. Sufficient for MVP concurrency needs.**
MVP limit: 1 active job per session/IP. Peak concurrency is 1–5 concurrent jobs globally. RQ handles this trivially.

**5. Transparent job inspection.**
`rq info` and `rq worker` are simple CLI tools. Job state (queued/started/finished/failed) is inspectable with minimal code.

**6. First-class Python 3 and async-friendly.**
RQ works cleanly with FastAPI's event loop. No legacy Python 2 compatibility baggage.

### Celery Disadvantages for This Use Case

- Requires configuring a result backend separately (even if it's also Redis)
- Worker startup is heavier (`celery -A app worker`)
- Routing, rate limiting, chords, and chains are powerful but complex — none needed for MVP
- More surface area for configuration drift between dev and prod
- Documentation is extensive because the problem space is large — overkill for our use case

### Acknowledged RQ Limitations

- No built-in scheduled/periodic tasks (use `rq-scheduler` or a simple cron container if needed in MVP2)
- No multi-broker support (only Redis) — not a constraint for us
- Smaller community than Celery — but sufficient documentation and active maintenance

---

## Consequences

**Positive:**
- Simpler docker-compose (no separate broker container)
- Less code, fewer configuration files
- Faster onboarding for new contributors
- Job status stored in Redis and mirrored in PostgreSQL — two sources of truth

**Negative / Accepted Trade-offs:**
- If we need complex task routing, chains, or ETA scheduling in MVP2/3, we may need `rq-scheduler` or re-evaluate
- RQ does not support non-Redis brokers — a constraint if we ever need RabbitMQ (unlikely)

**Migration path:**
If RQ becomes a bottleneck or lacks required features in MVP2+, migration to Celery is straightforward: jobs are plain Python functions, the interface change is only at enqueue/status call sites.
