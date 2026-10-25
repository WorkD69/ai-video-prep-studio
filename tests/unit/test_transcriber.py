"""Unit tests for app/pipeline/transcriber.py (ADR 006).

faster_whisper is replaced by a fake module in sys.modules — no model, no GPU.
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.pipeline.transcriber import (  # noqa: E402
    FasterWhisperTranscriber,
    MockTranscriber,
    build_hotwords,
    get_transcriber,
)


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    """Install a fake faster_whisper module; return the WhisperModel mock."""
    seg = types.SimpleNamespace(start=1.23456, end=4.5, text="  Привет, Claude Code  ", no_speech_prob=0.02)
    info = types.SimpleNamespace(language="ru")
    model_instance = MagicMock()
    model_instance.transcribe.return_value = (iter([seg]), info)
    whisper_model_cls = MagicMock(return_value=model_instance)
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = whisper_model_cls
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return whisper_model_cls


class TestFasterWhisperTranscriber:
    def test_maps_segments(self, fake_faster_whisper):
        segs = FasterWhisperTranscriber("turbo").transcribe("/tmp/a.wav")
        assert len(segs) == 1
        assert segs[0].start == 1.235
        assert segs[0].end == 4.5
        assert segs[0].text == "Привет, Claude Code"
        assert segs[0].no_speech_prob == 0.02

    def test_records_detected_language(self, fake_faster_whisper):
        t = FasterWhisperTranscriber("turbo")
        t.transcribe("/tmp/a.wav")
        assert t.detected_language == "ru"

    def test_model_and_decode_options(self, fake_faster_whisper):
        FasterWhisperTranscriber("turbo", language="ru", hotwords="Claude, MCP").transcribe("/tmp/a.wav")
        fake_faster_whisper.assert_called_once_with("turbo", device="auto", compute_type="auto")
        kwargs = fake_faster_whisper.return_value.transcribe.call_args.kwargs
        assert kwargs["language"] == "ru"
        assert kwargs["vad_filter"] is True
        assert kwargs["condition_on_previous_text"] is False
        assert kwargs["hotwords"] == "Claude, MCP"

    def test_auto_language_and_no_hotwords_pass_none(self, fake_faster_whisper):
        FasterWhisperTranscriber("turbo", language=None, hotwords="").transcribe("/tmp/a.wav")
        kwargs = fake_faster_whisper.return_value.transcribe.call_args.kwargs
        assert kwargs["language"] is None
        assert kwargs["hotwords"] is None


class TestBuildHotwords:
    def test_merges_and_dedupes_case_insensitive(self):
        assert build_hotwords("Claude, GitHub", "github,  MCP ", None) == "Claude, GitHub, MCP"

    def test_accepts_newlines(self):
        assert build_hotwords("Claude\nCursor\n\n") == "Claude, Cursor"

    def test_caps_length(self):
        result = build_hotwords(", ".join(f"term{i}" for i in range(500)))
        assert 0 < len(result) <= 400
        assert result.startswith("term0, term1")

    def test_empty(self):
        assert build_hotwords(None, "", " , ") == ""


class TestGetTranscriber:
    def test_mock_backend(self, monkeypatch):
        import app.pipeline.transcriber as tr

        monkeypatch.setattr(tr.settings, "transcriber_backend", "mock")
        assert isinstance(get_transcriber(), MockTranscriber)

    def test_faster_whisper_backend(self, monkeypatch):
        import app.pipeline.transcriber as tr

        monkeypatch.setattr(tr.settings, "transcriber_backend", "faster_whisper")
        monkeypatch.setattr(tr.settings, "whisper_model", "turbo")
        t = get_transcriber(language="en", hotwords="Claude")
        assert isinstance(t, FasterWhisperTranscriber)
        assert (t._model, t._language, t._hotwords) == ("turbo", "en", "Claude")
