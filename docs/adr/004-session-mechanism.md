# ADR 004 — Session Mechanism: Signed Browser Cookie

**Status:** Accepted
**Date:** 2026-06-07
**Deciders:** Project founder (Artem)
**Supersedes:** the M002 note in backend-agent.md ("server-generated anonymous UUID4 per upload")

---

## Context

`CLAUDE.md` sets an MVP hard limit: **1 active job per session/IP**. The enforcement primitive is a
stable session identity. Today there is none:

- `app/api/jobs.py` sets `session_id = str(uuid4())` on every upload — a fresh value per request.
- The browser never persists it; it is only echoed back in the JSON `UploadResponse`.
- Therefore "1 active job per session" cannot be enforced — every upload is a new session.

`backend-agent.md` and `security-agent.md` both anticipated this: the M002 `session_id` was
explicitly documented as *metadata only, not an auth/security boundary*, with "signed sessions and
cookies deferred to a future milestone". This ADR is that decision.

We must choose how a browser is identified across requests so the job limit can be applied.

---

## Decision

**Use a signed browser cookie carrying a server-minted session UUID.**

- Cookie name: `aivps_session` (configurable).
- Value: `"{session_id}.{signature}"` where `session_id` is a UUID4 and `signature` is a URL-safe
  base64 of `HMAC-SHA256(SECRET_KEY, session_id)`, computed with the **Python standard library only**
  (`hmac`, `hashlib`, `base64`). **No new dependency** — we do not rely on `itsdangerous` (its
  presence as a transitive dependency is not contractual). Verification uses `hmac.compare_digest`
  (constant-time).
- On a request without a valid signed cookie, the server **mints** a new `session_id` and sets the
  cookie on the response.
- On a request with an **absent / tampered / invalid** cookie, the server **rejects** the value
  silently and mints a fresh session (never 500). **Expiry is browser-side via `Max-Age`** — the HMAC
  carries no timestamp, so the server does not (and cannot) reject an HMAC-valid cookie as "expired";
  an HMAC-valid cookie the browser still sends is always accepted.
- Cookie attributes: `HttpOnly=True`, `SameSite=Lax`, `Secure` configurable (default `False` for the
  MVP HTTP-only local deploy; set `True` behind HTTPS in prod), `Max-Age` ~30 days.
- The raw `session_id` (unsigned UUID string) continues to be stored in `jobs.session_id`
  (`String(255)` — no column change). The cookie carries the signed form; the DB stores the raw UUID.

This **supersedes** the "fresh uuid4 per upload" behavior. `session_id` becomes stable per browser.

---

## Rationale

**1. Signed > unsigned: integrity, not secrecy.**
The session_id is not secret, but signing makes the cookie **tamper-evident**: a client cannot forge
*another* browser's session_id (which would let them grief that session's job slot) without the
`SECRET_KEY`. This satisfies `security-agent.md` §6 ("Session ID must come from signed cookie or HMAC
of a server-known value").

**2. Cookie > IP for the primary mechanism.**
IP-based limiting is rejected as primary because:
- NAT / corporate / mobile-carrier networks put many distinct users behind one IP → false blocks.
- `X-Forwarded-For` is client-spoofable and we run no trusted reverse proxy in MVP (ADR 001 deploy is
  bare `docker compose`, HTTP). Human decision: **do not trust `X-Forwarded-For` in MVP.**
- Cookies give per-browser granularity without proxy-trust assumptions.

**3. Honest about the threat model.**
A cookie-based limit is a **soft fairness control, not a hard security boundary**. A determined user
can clear cookies or use incognito to get a new session and bypass the limit. This is **accepted for
MVP**: the goal is preventing accidental double-submits and casual resource hogging, not defeating a
motivated attacker. Hard controls (authenticated accounts, IP rate-limiting behind a trusted proxy)
are deferred to MVP2 ("Rate limiting hardening" in ROADMAP) / the future auth milestone.

**4. Reuses existing primitives, zero new dependencies.**
`SECRET_KEY` already exists and is required by config. Signing uses only the Python stdlib (`hmac`,
`hashlib`, `base64`) — nothing added to `requirements.txt`. No schema change to `jobs.session_id`.
Minimal blast radius.

**Rejected alternatives:**
- **Unsigned cookie** — trivially forgeable; a client could set an arbitrary/another user's
  session_id. Rejected: signing is nearly free and closes session-forgery.
- **Starlette `SessionMiddleware`** — full server session framework is overkill; we need exactly one
  signed value, not a session dict. Rejected for surface-area minimalism.
- **IP / `X-Forwarded-For` (primary)** — NAT collisions + spoofable header + no trusted proxy.
  Rejected as primary (human decision). May return as a *secondary* signal in MVP2 hardening.
- **Server-side session store (Redis)** — unnecessary state for a stateless signed token; adds a
  Redis round-trip per request. Rejected for MVP.
- **Timestamped/expiring signed token (itsdangerous `TimestampSigner` or HMAC-over-timestamp)** —
  would allow server-side expiry, but adds complexity for no MVP benefit: browser `Max-Age` already
  drops the cookie, and a stable long-lived per-browser id is exactly what the fairness limit wants.
  Rejected for MVP simplicity.

---

## Consequences

**Positive:**
- "1 active job per session" becomes enforceable (the M008 milestone).
- Stable per-browser identity with no server-side session store.
- Tamper-evident; consistent with security-agent guidance.
- No DB schema change for the cookie itself; no new top-level dependency.

**Negative / accepted trade-offs:**
- Bypassable by clearing cookies / incognito — **accepted** for MVP (soft control).
- Cookie-disabled browsers get a new session each request → effectively no limit for them
  (acceptable; rare, and the limit is a fairness measure).
- No server-side expiry: an HMAC-valid cookie is accepted for as long as the browser keeps sending it
  (browser drops it at `Max-Age`). Acceptable — `SECRET_KEY` rotation invalidates all cookies if ever
  needed.
- `Secure=False` default means the cookie is sent over plain HTTP in the MVP local deploy — acceptable
  for localhost/HTTP MVP; **must** be flipped to `Secure=True` once HTTPS is added (MVP2 nginx/TLS).

**Migration path:**
- MVP2: add IP-based rate limiting behind a trusted reverse proxy (then `X-Forwarded-For` can be
  trusted from the known proxy hop only) as defense-in-depth on top of the cookie.
- Future auth milestone: tie `jobs.user_id` to authenticated users; the limit can key on `user_id`
  when present, falling back to the cookie session for anonymous users.

---

## Security notes (for security-agent)

- Cookie signed with `SECRET_KEY`; **absent / invalid / tampered** → silent fresh session, **never**
  a 500. There is no server-side "expired" rejection (expiry is browser-side via `Max-Age`).
- `HttpOnly` (no JS access), `SameSite=Lax` (CSRF-resistant for this non-mutating-by-GET flow).
- The session_id is a random UUID — non-sensitive — but the **signed cookie value must not be logged**.
- This ADR establishes the mechanism; the **atomic enforcement** (advisory xact lock, TOCTOU-safety)
  is specified and reviewed in M008.
