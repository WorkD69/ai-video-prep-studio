"""Tests for session cookie helpers (M008) and advisory lock key.

All tests are deterministic, no DB, no network.
The tested modules (app.services.session, app.services.active_job) do not yet
exist when this file is first run — that is the expected RED state.
"""
import os
import uuid

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.services.session import (  # noqa: E402
    mint_session_id,
    sign_session_id,
    read_session_id,
    set_session_cookie,
    resolve_session,
)
from app.services.active_job import session_lock_key  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(cookie_value: str | None = None):
    """Minimal mock of a FastAPI Request with a cookies dict."""
    req = MagicMock()
    if cookie_value is not None:
        req.cookies = {"aivps_session": cookie_value}
    else:
        req.cookies = {}
    return req


# ---------------------------------------------------------------------------
# test_session.py tests
# ---------------------------------------------------------------------------

def test_sign_verify_roundtrip():
    """read_session_id recovers the id after sign_session_id."""
    sid = mint_session_id()
    signed = sign_session_id(sid)
    req = _make_request(signed)
    assert read_session_id(req) == sid


def test_tampered_cookie_returns_none():
    """Corrupted cookie value → read_session_id returns None, no exception."""
    sid = mint_session_id()
    signed = sign_session_id(sid)
    # Corrupt the last 5 chars of the signature
    tampered = signed[:-5] + "XXXXX"
    req = _make_request(tampered)
    assert read_session_id(req) is None


def test_missing_cookie_returns_none():
    """No cookie in request → read_session_id returns None."""
    req = _make_request(None)
    assert read_session_id(req) is None


def test_malformed_cookie_no_dot_returns_none():
    """Cookie without a dot separator → None, no exception."""
    req = _make_request("nodotvalue")
    assert read_session_id(req) is None


def test_resolve_mints_when_absent():
    """resolve_session returns (uuid, is_new=True) when no cookie present."""
    req = _make_request(None)
    sid, is_new = resolve_session(req)
    assert is_new is True
    uuid.UUID(sid)  # must be a valid UUID string


def test_resolve_returns_existing_when_present():
    """resolve_session returns (existing_sid, is_new=False) for a valid cookie."""
    original_sid = mint_session_id()
    signed = sign_session_id(original_sid)
    req = _make_request(signed)
    sid, is_new = resolve_session(req)
    assert sid == original_sid
    assert is_new is False


def test_set_cookie_attributes():
    """set_session_cookie sets HttpOnly, SameSite=Lax, configured Max-Age/Secure."""
    from app.config import settings

    response = MagicMock()
    sid = mint_session_id()
    set_session_cookie(response, sid)

    response.set_cookie.assert_called_once()
    kw = response.set_cookie.call_args[1]

    assert kw["key"] == settings.session_cookie_name
    assert kw["httponly"] is True
    assert kw["samesite"] == "lax"
    assert kw["max_age"] == settings.session_cookie_max_age
    assert kw["secure"] == settings.session_cookie_secure
    assert kw["path"] == "/"
    # value must be the signed form (contains a dot)
    assert "." in kw["value"]


def test_session_lock_key_deterministic():
    """session_lock_key(x) is stable across calls and fits in signed int64."""
    sid = mint_session_id()
    key1 = session_lock_key(sid)
    key2 = session_lock_key(sid)
    assert key1 == key2
    # signed int64 range
    assert -(2**63) <= key1 < 2**63


def test_session_lock_key_different_sessions():
    """Two different session_ids should (almost certainly) produce different keys."""
    sid1 = mint_session_id()
    sid2 = mint_session_id()
    # With blake2b-8 collisions are astronomically rare for random UUIDs
    assert session_lock_key(sid1) != session_lock_key(sid2)


def test_set_cookie_value_is_signed():
    """Cookie value set by set_session_cookie can be read back by read_session_id."""
    response = MagicMock()
    sid = mint_session_id()
    set_session_cookie(response, sid)

    signed_value = response.set_cookie.call_args[1]["value"]
    req = _make_request(signed_value)
    assert read_session_id(req) == sid
