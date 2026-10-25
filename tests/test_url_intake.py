"""Link intake (ADR 006): URL validation / SSRF guard, probe mapping,
subtitle-language choice, POST /jobs/from-url, Origin/Host guards.

yt-dlp and DNS are mocked — no network.
"""

import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.pipeline import url_source
from app.pipeline.url_source import (
    UrlProbe,
    UrlSourceError,
    _to_probe,
    pick_manual_subtitle_lang,
    validate_url,
)
from tests.test_upload import VALID_MP4_CONTENT, MockDB, _make_mock_queue


# ---------------------------------------------------------------------------
# validate_url — SSRF guard
# ---------------------------------------------------------------------------


def _resolve_to(monkeypatch, *addresses):
    def fake_getaddrinfo(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", (a, port)) for a in addresses]

    monkeypatch.setattr(url_source.socket, "getaddrinfo", fake_getaddrinfo)


class TestValidateUrl:
    def test_public_https_ok(self, monkeypatch):
        _resolve_to(monkeypatch, "142.250.74.14")
        assert validate_url("  https://www.youtube.com/watch?v=x  ") == "https://www.youtube.com/watch?v=x"

    @pytest.mark.parametrize("url", [
        "", "ftp://example.com/v.mp4", "file:///etc/passwd", "javascript:alert(1)",
        "--exec=calc", "https://", "https://exa mple.com/", "x" * 3000,
    ])
    def test_invalid(self, monkeypatch, url):
        _resolve_to(monkeypatch, "142.250.74.14")
        with pytest.raises(UrlSourceError, match="invalid_url"):
            validate_url(url)

    @pytest.mark.parametrize("address", [
        "127.0.0.1", "10.0.0.5", "172.18.0.3", "192.168.1.1", "169.254.169.254",
        "0.0.0.0", "::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1",
    ])
    def test_private_addresses_forbidden(self, monkeypatch, address):
        _resolve_to(monkeypatch, address)
        with pytest.raises(UrlSourceError, match="forbidden_host"):
            validate_url("http://internal.example/video")

    def test_any_private_address_in_set_forbidden(self, monkeypatch):
        _resolve_to(monkeypatch, "142.250.74.14", "10.0.0.1")
        with pytest.raises(UrlSourceError, match="forbidden_host"):
            validate_url("https://mixed.example/")

    def test_unresolvable(self, monkeypatch):
        def boom(*a, **k):
            raise socket.gaierror("nope")

        monkeypatch.setattr(url_source.socket, "getaddrinfo", boom)
        with pytest.raises(UrlSourceError, match="invalid_url"):
            validate_url("https://no-such-host.invalid/")


# ---------------------------------------------------------------------------
# probe mapping + subtitle language choice
# ---------------------------------------------------------------------------


class TestToProbe:
    def test_maps_fields(self):
        probe = _to_probe({
            "title": " Lecture ", "duration": 3063, "language": "ru",
            "subtitles": {"ru": [{}], "live_chat": [{}]},
            "automatic_captions": {"en": [{}]},
            "chapters": [{"start_time": 0, "title": "Intro"}, {"start_time": 34.0, "title": " "}],
            "channel": "Chan", "webpage_url": "https://y/x",
        })
        assert probe.title == "Lecture"
        assert probe.duration == 3063.0
        assert probe.manual_subtitle_langs == ["ru"]  # auto captions and live chat ignored
        assert probe.chapters == [{"start": 0.0, "title": "Intro"}]
        assert probe.channel == "Chan"

    @pytest.mark.parametrize("info,code", [
        ({"_type": "playlist", "duration": 10}, "playlist_not_supported"),
        ({"entries": [{}], "duration": 10}, "playlist_not_supported"),
        ({"is_live": True, "duration": 10}, "live_not_supported"),
        ({"live_status": "is_upcoming", "duration": 10}, "live_not_supported"),
        ({"duration": None}, "unknown_duration"),
        ({"duration": 0}, "unknown_duration"),
    ])
    def test_rejections(self, info, code):
        with pytest.raises(UrlSourceError, match=code):
            _to_probe(info)


class TestPickManualSubtitleLang:
    def test_exact(self):
        assert pick_manual_subtitle_lang(["en", "ru"], "ru") == "ru"

    def test_regional_variant(self):
        assert pick_manual_subtitle_lang(["en-US"], "en") == "en-US"

    def test_other_language_is_a_translation_not_used(self):
        assert pick_manual_subtitle_lang(["en"], "ru") is None

    def test_unknown_language(self):
        assert pick_manual_subtitle_lang(["ru"], None) is None


# ---------------------------------------------------------------------------
# POST /jobs/from-url
# ---------------------------------------------------------------------------


class FakeSettings:
    def __init__(self, upload_dir: str):
        self.upload_dir = upload_dir
        self.max_upload_bytes = 5 * 1024**3
        self.max_duration_seconds = 10800
        self.rq_job_timeout = 14400
        self.session_cookie_name = "aivps_session"
        self.session_cookie_max_age = 2592000
        self.session_cookie_secure = False


def _probe(duration=3063.0):
    return UrlProbe(title="Claude Code за 50 минут", duration=duration, language="ru",
                    manual_subtitle_langs=["ru"])


@pytest.fixture
def url_client(tmp_path):
    mock_db = MockDB()

    def override():
        yield mock_db

    app.dependency_overrides[get_db] = override
    upload_dir = tmp_path / "uploads"
    mock_queue = _make_mock_queue()
    with patch("app.api.jobs.settings", FakeSettings(str(upload_dir))), \
         patch("app.api.jobs.get_queue", return_value=mock_queue), \
         patch("app.api.jobs.validate_url", side_effect=lambda u: u.strip()), \
         patch("app.api.jobs.probe_url", return_value=_probe()) as probe_mock:
        with TestClient(app) as client:
            yield client, mock_db, upload_dir, mock_queue, probe_mock
    app.dependency_overrides.clear()


def _only_job(mock_db):
    jobs = list(mock_db._store.values())
    assert len(jobs) == 1
    return jobs[0]


def test_from_url_happy_path(url_client):
    client, mock_db, upload_dir, mock_queue, _ = url_client
    resp = client.post("/jobs/from-url", data={
        "url": "https://www.youtube.com/watch?v=izH9evNQ7j0", "language": "RU", "terms": "Cowork, Ultracode"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "queued"
    assert body["original_filename"] == "Claude Code за 50 минут"
    assert body["video_size_bytes"] is None

    job = _only_job(mock_db)
    assert job.source_type == "url"
    assert job.source_url == "https://www.youtube.com/watch?v=izH9evNQ7j0"
    assert job.language == "ru"
    assert job.glossary == "Cowork, Ultracode"
    assert job.duration_seconds == 3063.0
    assert Path(job.input_path).parent == upload_dir.resolve()
    assert Path(job.input_path).name == job.stored_filename
    assert job.stored_filename.endswith(".mp4")
    mock_queue.enqueue.assert_called_once()


def test_from_url_auto_language_stored_as_none(url_client):
    client, mock_db, *_ = url_client
    assert client.post("/jobs/from-url", data={"url": "https://y/x"}).status_code == 201
    assert _only_job(mock_db).language is None


def test_from_url_htmx_redirects(url_client):
    client, mock_db, *_ = url_client
    resp = client.post("/jobs/from-url", data={"url": "https://y/x"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == f"/status/{_only_job(mock_db).id}"


def test_from_url_over_duration_limit_422(url_client):
    client, mock_db, _, mock_queue, probe_mock = url_client
    probe_mock.return_value = _probe(duration=10801)
    resp = client.post("/jobs/from-url", data={"url": "https://y/x"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Video exceeds the 3-hour limit"
    assert mock_db._store == {}
    mock_queue.enqueue.assert_not_called()


def test_from_url_exactly_limit_allowed(url_client):
    client, _, _, _, probe_mock = url_client
    probe_mock.return_value = _probe(duration=10800)
    assert client.post("/jobs/from-url", data={"url": "https://y/x"}).status_code == 201


@pytest.mark.parametrize("code,message", [
    ("forbidden_host", "This link points to a local or private address"),
    ("unreadable_link", "Could not read this link (unsupported site, private or removed video)"),
    ("playlist_not_supported", "Playlists are not supported: link a single video"),
])
def test_from_url_probe_errors_400(url_client, code, message):
    client, mock_db, _, _, probe_mock = url_client
    probe_mock.side_effect = UrlSourceError(code)
    resp = client.post("/jobs/from-url", data={"url": "https://y/x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == message
    assert mock_db._store == {}


def test_from_url_error_htmx_fragment(url_client):
    client, _, _, _, probe_mock = url_client
    probe_mock.side_effect = UrlSourceError("forbidden_host")
    resp = client.post("/jobs/from-url", data={"url": "http://127.0.0.1/"}, headers={"HX-Request": "true"})
    assert resp.status_code == 400
    assert "local or private address" in resp.text


def test_from_url_invalid_language_422(url_client):
    client, mock_db, *_ = url_client
    resp = client.post("/jobs/from-url", data={"url": "https://y/x", "language": "ru; drop"})
    assert resp.status_code == 422
    assert mock_db._store == {}


def test_from_url_terms_capped_and_cleaned(url_client):
    client, mock_db, *_ = url_client
    terms = "Claude\x00\x07, " + "x" * 2000
    assert client.post("/jobs/from-url", data={"url": "https://y/x", "terms": terms}).status_code == 201
    glossary = _only_job(mock_db).glossary
    assert "\x00" not in glossary and "\x07" not in glossary
    assert len(glossary) == 500


def test_from_url_active_job_429(url_client):
    client, mock_db, _, _, probe_mock = url_client
    with patch("app.api.jobs.find_active_job", return_value=MagicMock(id="abc")):
        resp = client.post("/jobs/from-url", data={"url": "https://y/x"})
    assert resp.status_code == 429
    probe_mock.assert_not_called()  # fast-fail before the network probe


def test_upload_accepts_language_and_terms(tmp_path):
    from tests.test_upload import FakeSettings as UploadSettings

    mock_db = MockDB()

    def override():
        yield mock_db

    app.dependency_overrides[get_db] = override
    try:
        with patch("app.api.jobs.settings", UploadSettings(str(tmp_path / "u"), 524288000)), \
             patch("app.api.jobs.get_queue", return_value=_make_mock_queue()):
            with TestClient(app) as client:
                resp = client.post(
                    "/jobs/upload",
                    files={"file": ("lecture.mp4", VALID_MP4_CONTENT, "video/mp4")},
                    data={"language": "en", "terms": "MCP"},
                )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201
    job = _only_job(mock_db)
    assert (job.source_type, job.language, job.glossary) == ("file", "en", "MCP")


# ---------------------------------------------------------------------------
# Origin (CSRF) and Host (DNS rebinding) guards
# ---------------------------------------------------------------------------


def test_cross_origin_post_rejected(url_client):
    client, mock_db, *_ = url_client
    resp = client.post("/jobs/from-url", data={"url": "https://y/x"}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "cross_origin_rejected"
    assert mock_db._store == {}


def test_null_origin_rejected(url_client):
    client, *_ = url_client
    resp = client.post("/jobs/from-url", data={"url": "https://y/x"}, headers={"Origin": "null"})
    assert resp.status_code == 403


def test_same_origin_post_allowed(url_client):
    client, *_ = url_client
    resp = client.post("/jobs/from-url", data={"url": "https://y/x"}, headers={"Origin": "http://localhost:8000"})
    assert resp.status_code == 201


def test_foreign_host_header_rejected():
    with TestClient(app) as client:
        resp = client.get("/", headers={"Host": "attacker.example"})
    assert resp.status_code == 400
