"""Integration tests for the v2 worker pipeline (ADR 006) — fakes only.

No ffmpeg, no crv, no whisper, no network: FakeMediaProber, FakeFrameSelector,
MockTranscriber (TRANSCRIBER_BACKEND=mock) and a patched download_url.

Covers:
- package invariant: frames inlined in transcript.md == files == manifest rows
- file vs URL jobs, author's subtitles vs whisper, empty-subtitles fallback
- stage callbacks, hotwords, fallback frame selector, path validation, cleanup
"""

import csv
import io
import json
import os
import re
import zipfile
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.pipeline.frames import FakeFrameSelector, FixedIntervalFrameSelector  # noqa: E402
from app.pipeline.media import FakeMediaProber, FakeScreenshotExtractor, MediaError  # noqa: E402
from app.pipeline.transcriber import MockTranscriber  # noqa: E402
from app.pipeline.url_source import UrlDownload, UrlProbe  # noqa: E402

VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
Привет, это Claude Code.

00:00:30.000 --> 00:00:35.000
Вторая глава.
"""


def _make_job(upload_dir, *, source_type="file", duration_seconds=75.0, create_file=True):
    """Job-like mock whose input path is a UUID name under upload_dir."""
    job = MagicMock()
    job.id = uuid4()
    job.original_filename = "lecture.mp4" if source_type == "file" else "Claude Code за 50 минут"
    stored = f"{uuid4()}.mp4"
    job.stored_filename = stored
    upload_dir.mkdir(parents=True, exist_ok=True)
    input_file = upload_dir / stored
    if create_file:
        input_file.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
    job.input_path = str(input_file)
    job.duration_seconds = duration_seconds
    job.source_type = source_type
    job.source_url = "https://www.youtube.com/watch?v=x" if source_type == "url" else None
    job.language = None
    job.glossary = None
    return job


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    """Point settings at tmp dirs; force the mock transcriber."""
    import app.pipeline.media_pipeline as mpl
    import app.pipeline.transcriber as tr

    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    monkeypatch.setattr(mpl.settings, "upload_dir", str(upload_dir))
    monkeypatch.setattr(mpl.settings, "output_dir", str(output_dir))
    monkeypatch.setattr(tr.settings, "transcriber_backend", "mock")
    return upload_dir, output_dir


def _fake_download(subtitles: str | None, chapters=None):
    """Stand-in for download_url: writes the video (and subtitles) like yt-dlp would."""

    def download(url, video_path, subtitles_dir, language, max_bytes):
        video_path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
        sub_path = None
        if subtitles is not None:
            subtitles_dir.mkdir(parents=True, exist_ok=True)
            sub_path = subtitles_dir / "subtitles.ru.vtt"
            sub_path.write_bytes(subtitles.encode("utf-8"))
        probe = UrlProbe(
            title="Claude Code за 50 минут", duration=75.0, language="ru",
            manual_subtitle_langs=["ru"] if subtitles is not None else [],
            chapters=chapters or [], channel="Chan", webpage_url=url,
        )
        return UrlDownload(video_path=video_path, subtitle_path=sub_path,
                           subtitle_lang="ru" if sub_path else None, probe=probe)

    return download


def _read_zip(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _run(job, selector=None, fallback=None, on_stage=None, prober=None):
    from app.pipeline.media_pipeline import run_media_pipeline

    return run_media_pipeline(job, prober or FakeMediaProber(), selector or FakeFrameSelector(),
                              fallback, on_stage=on_stage)


# ---------------------------------------------------------------------------
# file jobs
# ---------------------------------------------------------------------------


class TestFileJob:
    def test_package_entries(self, pipeline_env):
        upload_dir, _ = pipeline_env
        files = _read_zip(_run(_make_job(upload_dir)))
        for name in ("README.md", "transcript.md", "transcript.json", "frames_manifest.csv",
                     "failed_parts.md", "metadata.json", "frames/"):
            assert name in files
        assert not any(name.startswith("subtitles.") for name in files)

    def test_invariant_inline_frames_files_manifest_metadata(self, pipeline_env):
        upload_dir, _ = pipeline_env
        files = _read_zip(_run(_make_job(upload_dir, duration_seconds=75.0)))
        frame_files = sorted(n.split("/", 1)[1] for n in files if n.startswith("frames/frame_"))
        inline = sorted(re.findall(r"\(frames/(frame_\d{6}s\.jpg)\)", files["transcript.md"].decode()))
        manifest = [row["filename"] for row in csv.DictReader(io.StringIO(files["frames_manifest.csv"].decode()))]
        metadata = json.loads(files["metadata.json"])
        assert frame_files == inline == sorted(manifest)
        assert metadata["frame_count"] == len(frame_files) == 4  # fake: 0, 20, 40, 60

    def test_metadata_describes_processing(self, pipeline_env):
        upload_dir, _ = pipeline_env
        metadata = json.loads(_read_zip(_run(_make_job(upload_dir)))["metadata.json"])
        assert metadata["source_type"] == "file"
        assert metadata["transcript_source"] == "mock"
        assert metadata["frames_mode"] == "fake"
        assert metadata["title"] == "lecture"
        assert metadata["duration_seconds"] == 75.0

    def test_zip_named_after_file(self, pipeline_env):
        upload_dir, _ = pipeline_env
        assert _run(_make_job(upload_dir)).name.startswith("llm_analysis_package_lecture_")

    def test_duration_none_probes_in_worker(self, pipeline_env):
        upload_dir, _ = pipeline_env
        job = _make_job(upload_dir, duration_seconds=None)
        metadata = json.loads(_read_zip(_run(job, prober=FakeMediaProber(duration=50.0)))["metadata.json"])
        assert metadata["duration_seconds"] == 50.0
        assert metadata["frame_count"] == 3

    def test_stages_reported(self, pipeline_env):
        upload_dir, _ = pipeline_env
        stages = []
        _run(_make_job(upload_dir), on_stage=stages.append)
        assert stages == ["frames", "transcribing", "packaging"]

    def test_fallback_selector_used_when_primary_fails(self, pipeline_env):
        upload_dir, _ = pipeline_env

        class Exploding:
            mode = "scene_change"

            def select(self, input_path, out_dir, duration):
                raise RuntimeError("crv failed")

        zip_path = _run(_make_job(upload_dir), selector=Exploding(),
                        fallback=FixedIntervalFrameSelector(FakeScreenshotExtractor()))
        metadata = json.loads(_read_zip(zip_path)["metadata.json"])
        assert metadata["frames_mode"] == "fixed_20s"
        assert metadata["frame_count"] == 4

    def test_hotwords_include_glossary_job_terms_and_title(self, pipeline_env, monkeypatch):
        import app.pipeline.media_pipeline as mpl

        upload_dir, _ = pipeline_env
        monkeypatch.setattr(mpl.settings, "whisper_glossary", "Claude, MCP")
        job = _make_job(upload_dir)
        job.glossary = "Cowork"
        job.language = "ru"
        captured = {}

        def fake_get_transcriber(language=None, hotwords=None):
            captured.update(language=language, hotwords=hotwords)
            return MockTranscriber()

        monkeypatch.setattr(mpl, "get_transcriber", fake_get_transcriber)
        _run(job)
        assert captured == {"language": "ru", "hotwords": "Claude, MCP, Cowork, lecture"}


# ---------------------------------------------------------------------------
# URL jobs
# ---------------------------------------------------------------------------


class TestUrlJob:
    def test_author_subtitles_used(self, pipeline_env):
        upload_dir, _ = pipeline_env
        job = _make_job(upload_dir, source_type="url", create_file=False)
        chapters = [{"start": 0.0, "title": "Начало"}, {"start": 30.0, "title": "Глава 2"}]
        with patch("app.pipeline.media_pipeline.download_url", side_effect=_fake_download(VTT, chapters)):
            files = _read_zip(_run(job))
        metadata = json.loads(files["metadata.json"])
        transcript = files["transcript.md"].decode("utf-8")
        assert metadata["transcript_source"] == "manual_subtitles"
        assert metadata["language"] == "ru"
        assert metadata["subtitles_file"] == "subtitles.ru.vtt"
        assert metadata["source_url"] == "https://www.youtube.com/watch?v=x"
        assert files["subtitles.ru.vtt"].decode("utf-8") == VTT
        assert "Привет, это Claude Code." in transcript
        assert transcript.index("## 00:00:00 — Начало") < transcript.index("## 00:00:30 — Глава 2")
        assert "author's subtitles" in files["README.md"].decode("utf-8")

    def test_no_subtitles_falls_back_to_transcriber(self, pipeline_env):
        upload_dir, _ = pipeline_env
        job = _make_job(upload_dir, source_type="url", create_file=False)
        with patch("app.pipeline.media_pipeline.download_url", side_effect=_fake_download(None)):
            metadata = json.loads(_read_zip(_run(job))["metadata.json"])
        assert metadata["transcript_source"] == "mock"
        assert metadata["language"] == "ru"  # hint from the site metadata
        assert metadata["channel"] == "Chan"

    def test_empty_subtitles_fall_back_to_transcriber(self, pipeline_env):
        upload_dir, _ = pipeline_env
        job = _make_job(upload_dir, source_type="url", create_file=False)
        with patch("app.pipeline.media_pipeline.download_url", side_effect=_fake_download("WEBVTT\n")):
            files = _read_zip(_run(job))
        assert json.loads(files["metadata.json"])["transcript_source"] == "mock"
        assert "subtitles.ru.vtt" not in files

    def test_stages_include_download(self, pipeline_env):
        upload_dir, _ = pipeline_env
        stages = []
        job = _make_job(upload_dir, source_type="url", create_file=False)
        with patch("app.pipeline.media_pipeline.download_url", side_effect=_fake_download(VTT)):
            _run(job, on_stage=stages.append)
        assert stages == ["downloading", "frames", "transcribing", "packaging"]

    def test_downloads_to_job_input_path(self, pipeline_env):
        upload_dir, _ = pipeline_env
        job = _make_job(upload_dir, source_type="url", create_file=False)
        download = MagicMock(side_effect=_fake_download(None))
        with patch("app.pipeline.media_pipeline.download_url", download):
            _run(job)
        args = download.call_args.args
        assert args[0] == job.source_url
        assert str(args[1]) == str((upload_dir / job.stored_filename).resolve())

    def test_zip_named_after_title(self, pipeline_env):
        upload_dir, _ = pipeline_env
        job = _make_job(upload_dir, source_type="url", create_file=False)
        with patch("app.pipeline.media_pipeline.download_url", side_effect=_fake_download(None)):
            zip_path = _run(job)
        assert zip_path.name.startswith("llm_analysis_package_Claude_Code_za_50_minut_")


# ---------------------------------------------------------------------------
# safety + cleanup
# ---------------------------------------------------------------------------


class TestSafety:
    def test_input_path_outside_upload_dir_raises(self, pipeline_env, tmp_path):
        upload_dir, _ = pipeline_env
        job = _make_job(upload_dir)
        outside = tmp_path / "elsewhere.mp4"
        outside.write_bytes(b"x")
        job.input_path = str(outside)
        with pytest.raises(MediaError, match="invalid_or_unreadable_media"):
            _run(job)

    def test_staging_cleaned_on_success(self, pipeline_env):
        upload_dir, output_dir = pipeline_env
        job = _make_job(upload_dir)
        _run(job)
        assert not (output_dir / "staging" / str(job.id)).exists()

    def test_staging_cleaned_on_failure(self, pipeline_env):
        upload_dir, output_dir = pipeline_env
        job = _make_job(upload_dir)

        class Exploding:
            mode = "scene_change"

            def select(self, input_path, out_dir, duration):
                raise MediaError("screenshot_extraction_failed")

        with pytest.raises(MediaError):
            _run(job, selector=Exploding())
        assert not (output_dir / "staging" / str(job.id)).exists()
