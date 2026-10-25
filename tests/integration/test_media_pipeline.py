"""Integration tests for M009 media pipeline (fake seam, no ffmpeg).

Covers:
- build_zip(..., screenshots_dir=...) with real frame files
- M004 placeholder path (screenshots_dir=None) stays intact
- run_media_pipeline invariant, staging cleanup, input path validation
"""

import csv
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.pipeline.media import (  # noqa: E402
    FakeMediaProber,
    FakeScreenshotExtractor,
    MediaError,
)
from app.pipeline.zip_packaging import build_zip  # noqa: E402

_FIXED_TS = datetime(2026, 6, 10, 10, 30, 0, tzinfo=timezone.utc)

_DOC_KWARGS = dict(
    transcript_md="# Transcript",
    transcript_json="[]",
    summary_input_md="# Summary",
    screenshots_manifest_csv="frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id",
    failed_parts_md="# Failed",
    metadata_json="{}",
)

# Distinctive frame bytes — a "real" JPEG payload different from the placeholder
_REAL_FRAME_BYTES = b"\xff\xd8\xff\xe0REAL_FRAME_CONTENT_BYTES\xff\xd9"


def _make_frames(dir_path, timestamps):
    dir_path.mkdir(parents=True, exist_ok=True)
    for sec in timestamps:
        (dir_path / f"frame_{sec:06d}s.jpg").write_bytes(_REAL_FRAME_BYTES)


# ---------------------------------------------------------------------------
# build_zip with screenshots_dir
# ---------------------------------------------------------------------------


class TestBuildZipWithScreenshotsDir:
    def test_adds_real_frame_files(self, tmp_path):
        shots = tmp_path / "shots"
        _make_frames(shots, [0, 20, 40])

        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            screenshot_count=3,
            screenshots_dir=shots,
            **_DOC_KWARGS,
        )

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            data = zf.read("screenshots/frame_000020s.jpg")

        assert {
            "screenshots/frame_000000s.jpg",
            "screenshots/frame_000020s.jpg",
            "screenshots/frame_000040s.jpg",
        }.issubset(names)
        assert data == _REAL_FRAME_BYTES  # real bytes, not the placeholder

    def test_count_mismatch_raises(self, tmp_path):
        shots = tmp_path / "shots"
        _make_frames(shots, [0, 20])

        with pytest.raises(ValueError):
            build_zip(
                output_dir=tmp_path,
                safe_stem="lecture",
                ts=_FIXED_TS,
                screenshot_count=3,  # invariant guard: 2 files != 3
                screenshots_dir=shots,
                **_DOC_KWARGS,
            )

    def test_count_mismatch_leaves_no_partial_zip(self, tmp_path):
        shots = tmp_path / "shots"
        _make_frames(shots, [0])

        with pytest.raises(ValueError):
            build_zip(
                output_dir=tmp_path,
                safe_stem="lecture",
                ts=_FIXED_TS,
                screenshot_count=5,
                screenshots_dir=shots,
                **_DOC_KWARGS,
            )
        assert list(tmp_path.glob("*.zip")) == []

    def test_none_keeps_placeholder_behaviour(self, tmp_path):
        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            screenshot_count=2,
            screenshots_dir=None,
            **_DOC_KWARGS,
        )

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            data = zf.read("screenshots/frame_000000s.jpg")

        assert "screenshots/frame_000020s.jpg" in names
        # placeholder path: tiny fixed JPEG
        assert data[:2] == b"\xff\xd8"
        assert data != _REAL_FRAME_BYTES

    def test_zero_screenshots_with_empty_dir(self, tmp_path):
        shots = tmp_path / "shots"
        shots.mkdir()

        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            screenshot_count=0,
            screenshots_dir=shots,
            **_DOC_KWARGS,
        )

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        frame_entries = [n for n in names if n.startswith("screenshots/") and n != "screenshots/"]
        assert "screenshots/" in names
        assert frame_entries == []

    def test_works_with_fake_extractor_output(self, tmp_path):
        """End-to-end seam: FakeScreenshotExtractor frames go into the ZIP."""
        shots = tmp_path / "shots"
        frames = FakeScreenshotExtractor().extract("/any.mp4", shots, [0, 20, 40, 60])

        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            screenshot_count=len(frames),
            screenshots_dir=shots,
            **_DOC_KWARGS,
        )

        with zipfile.ZipFile(zip_path) as zf:
            names = [n for n in zf.namelist() if n.startswith("screenshots/frame_")]
            sample = zf.read("screenshots/frame_000060s.jpg")

        assert len(names) == 4
        assert len(sample) > 0
        assert sample[:2] == b"\xff\xd8"


# ---------------------------------------------------------------------------
# run_media_pipeline (fakes)
# ---------------------------------------------------------------------------


def _make_pipeline_job(upload_dir, duration_seconds=75.0):
    """Job-like mock whose input file really exists under upload_dir."""
    job = MagicMock()
    job.id = uuid4()
    job.original_filename = "lecture.mp4"
    stored = f"{uuid4()}.mp4"
    job.stored_filename = stored
    upload_dir.mkdir(parents=True, exist_ok=True)
    input_file = upload_dir / stored
    input_file.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
    job.input_path = str(input_file)
    job.video_size_bytes = input_file.stat().st_size
    job.duration_seconds = duration_seconds
    return job


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    """Point settings at tmp dirs for media_pipeline."""
    import app.pipeline.media_pipeline as mpl

    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    monkeypatch.setattr(mpl.settings, "upload_dir", str(upload_dir))
    monkeypatch.setattr(mpl.settings, "output_dir", str(output_dir))
    return upload_dir, output_dir


class TestRunMediaPipeline:
    def test_invariant_metadata_manifest_files(self, pipeline_env):
        from app.pipeline.media_pipeline import run_media_pipeline

        upload_dir, output_dir = pipeline_env
        job = _make_pipeline_job(upload_dir, duration_seconds=75.0)

        zip_path = run_media_pipeline(job, FakeMediaProber(), FakeScreenshotExtractor())

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            metadata = json.loads(zf.read("metadata.json"))
            manifest_text = zf.read("screenshots_manifest.csv").decode()
            frame_entries = [n for n in names if n.startswith("screenshots/") and n != "screenshots/"]

        manifest_rows = list(csv.reader(io.StringIO(manifest_text)))[1:]
        assert metadata["screenshot_count"] == len(manifest_rows) == len(frame_entries) == 4
        assert metadata["duration_seconds"] == 75.0

    def test_zip_frames_are_extractor_bytes(self, pipeline_env):
        from app.pipeline.media_pipeline import run_media_pipeline

        upload_dir, _ = pipeline_env
        job = _make_pipeline_job(upload_dir, duration_seconds=75.0)

        zip_path = run_media_pipeline(job, FakeMediaProber(), FakeScreenshotExtractor())

        with zipfile.ZipFile(zip_path) as zf:
            data = zf.read("screenshots/frame_000000s.jpg")
        assert len(data) > 0
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_duration_none_probes_in_worker(self, pipeline_env):
        """Defensive path: job.duration_seconds is None -> prober supplies span."""
        from app.pipeline.media_pipeline import run_media_pipeline

        upload_dir, _ = pipeline_env
        job = _make_pipeline_job(upload_dir, duration_seconds=None)

        zip_path = run_media_pipeline(job, FakeMediaProber(duration=50.0), FakeScreenshotExtractor())

        with zipfile.ZipFile(zip_path) as zf:
            metadata = json.loads(zf.read("metadata.json"))
        assert metadata["duration_seconds"] == 50.0
        assert metadata["screenshot_count"] == 3  # 0, 20, 40

    def test_short_video_one_screenshot(self, pipeline_env):
        """AC16: < 20 s -> exactly one screenshot at 0 s, all counts consistent."""
        from app.pipeline.media_pipeline import run_media_pipeline

        upload_dir, _ = pipeline_env
        job = _make_pipeline_job(upload_dir, duration_seconds=15.0)

        zip_path = run_media_pipeline(job, FakeMediaProber(), FakeScreenshotExtractor())

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            metadata = json.loads(zf.read("metadata.json"))
            manifest_text = zf.read("screenshots_manifest.csv").decode()
            frame_entries = [n for n in names if n.startswith("screenshots/") and n != "screenshots/"]

        manifest_rows = list(csv.reader(io.StringIO(manifest_text)))[1:]
        assert frame_entries == ["screenshots/frame_000000s.jpg"]
        assert metadata["screenshot_count"] == len(manifest_rows) == 1

    def test_input_path_outside_upload_dir_raises_media_error(self, pipeline_env, tmp_path):
        from app.pipeline.media_pipeline import run_media_pipeline

        upload_dir, _ = pipeline_env
        job = _make_pipeline_job(upload_dir)
        evil = tmp_path / "elsewhere" / "evil.mp4"
        evil.parent.mkdir(parents=True, exist_ok=True)
        evil.write_bytes(b"x")
        job.input_path = str(evil)

        with pytest.raises(MediaError):
            run_media_pipeline(job, FakeMediaProber(), FakeScreenshotExtractor())

    def test_staging_cleaned_on_success(self, pipeline_env):
        from app.pipeline.media_pipeline import run_media_pipeline

        upload_dir, output_dir = pipeline_env
        job = _make_pipeline_job(upload_dir)

        run_media_pipeline(job, FakeMediaProber(), FakeScreenshotExtractor())

        assert not (output_dir / "staging" / str(job.id)).exists()

    def test_staging_cleaned_on_failure(self, pipeline_env):
        from app.pipeline.media_pipeline import run_media_pipeline

        upload_dir, output_dir = pipeline_env
        job = _make_pipeline_job(upload_dir)

        class ExplodingExtractor:
            def extract(self, input_path, out_dir, timestamps):
                raise MediaError("screenshot_extraction_failed")

        with pytest.raises(MediaError):
            run_media_pipeline(job, FakeMediaProber(), ExplodingExtractor())

        assert not (output_dir / "staging" / str(job.id)).exists()

    def test_transcriber_stays_mock(self, pipeline_env):
        """AC14: metadata transcriber/model_size stay MockTranscriber/mock."""
        from app.pipeline.media_pipeline import run_media_pipeline

        upload_dir, _ = pipeline_env
        job = _make_pipeline_job(upload_dir)

        zip_path = run_media_pipeline(job, FakeMediaProber(), FakeScreenshotExtractor())

        with zipfile.ZipFile(zip_path) as zf:
            metadata = json.loads(zf.read("metadata.json"))
        assert metadata["transcriber"] == "MockTranscriber"
        assert metadata["model_size"] == "mock"
