"""Signed session cookie helpers (ADR 004, M008).

All signing uses Python stdlib only (hmac, hashlib, base64).
No new dependency. SECRET_KEY is already required by config.
"""
import base64
import hashlib
import hmac
from uuid import uuid4

from app.config import settings


def mint_session_id() -> str:
    return str(uuid4())


def sign_session_id(session_id: str) -> str:
    """Return "{session_id}.{urlsafe_b64(HMAC-SHA256(SECRET_KEY, session_id))}"."""
    sig = hmac.new(
        settings.secret_key.encode(),
        session_id.encode(),
        hashlib.sha256,
    ).digest()
    b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{session_id}.{b64}"


def read_session_id(request) -> str | None:
    """Return the raw session_id from the signed cookie, or None if absent/invalid/tampered.

    Never raises. No server-side expiry check — an HMAC-valid cookie is always
    accepted; expiry is enforced browser-side via Max-Age (ADR 004).
    """
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None

    dot = raw.rfind(".")
    if dot < 0:
        return None

    session_id = raw[:dot]
    provided_b64 = raw[dot + 1:]

    try:
        # Add padding back before decoding
        padding = "=" * (-len(provided_b64) % 4)
        provided_sig = base64.urlsafe_b64decode(provided_b64 + padding)
    except Exception:
        return None

    expected_sig = hmac.new(
        settings.secret_key.encode(),
        session_id.encode(),
        hashlib.sha256,
    ).digest()

    if not hmac.compare_digest(expected_sig, provided_sig):
        return None

    return session_id


def set_session_cookie(response, session_id: str) -> None:
    """Set the signed session cookie on the given response object."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=sign_session_id(session_id),
        max_age=settings.session_cookie_max_age,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


def resolve_session(request) -> tuple[str, bool]:
    """Return (session_id, is_new). Mints a fresh id if cookie is absent or invalid."""
    existing = read_session_id(request)
    if existing is not None:
        return existing, False
    return mint_session_id(), True
