"""Integration tests for ZIP packaging (Milestone 004, Task 3).

Covers:
- make_safe_stem sanitization (path traversal, unicode, spaces, overlong)
- make_zip_filename format
- build_zip contents and invariants
- run_mock_output_pipeline end-to-end + staging cleanup
"""

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.pipeline.zip_packaging import (
    build_zip,
    make_safe_stem,
    make_zip_filename,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_job():
    job = MagicMock()
    job.id = uuid4()
    job.original_filename = "lecture.mp4"
    job.stored_filename = f"{uuid4()}.mp4"
    job.input_path = "/uploads/test.mp4"
    job.video_size_bytes = None
    job.duration_seconds = None
    return job


_FIXED_TS = datetime(2026, 6, 4, 10, 30, 0, tzinfo=timezone.utc)

_MINIMAL_ZIP_KWARGS = dict(
    transcript_md="# Transcript",
    transcript_json="[]",
    summary_input_md="# Summary",
    screenshots_manifest_csv="frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id",
    failed_parts_md="# Failed",
    metadata_json="{}",
    screenshot_count=0,
)


# ---------------------------------------------------------------------------
# make_safe_stem
# ---------------------------------------------------------------------------


class TestMakeSafeStem:
    def test_normal_filename(self):
        result = make_safe_stem("lecture.mp4", "fallback")
        assert result == "lecture"

    def test_path_traversal_stripped(self):
        result = make_safe_stem("../escape.mp4", "fallback")
        # ".." stem → "__" → stripped → empty → fallback, or sanitized
        # Path("../escape.mp4").stem == "escape" on most systems
        # Either way, result must not contain ".." or "/"
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result

    def test_dots_sanitized(self):
        result = make_safe_stem("my.lecture.video.mp4", "fallback")
        # stem = "my.lecture.video", dots replaced with _
        assert "." not in result

    def test_unicode_replaced(self):
        result = make_safe_stem("лекция_001.mp4", "fallback")
        assert all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in result)

    def test_spaces_replaced(self):
        result = make_safe_stem("my lecture video.mp4", "fallback")
        assert " " not in result

    def test_overlong_capped_at_64(self):
        long_name = "a" * 100 + ".mp4"
        result = make_safe_stem(long_name, "fallback")
        assert len(result) <= 64

    def test_overlong_no_trailing_underscore_after_cap(self):
        # stem = "a"*63 + "!" + "b" → after replace: "a"*63 + "_b"
        # cap at 64: "a"*63 + "_"  → after strip: "a"*63
        stem_str = "a" * 63 + "!" + "b"
        long_name = stem_str + ".mp4"
        result = make_safe_stem(long_name, "fallback")
        assert not result.endswith("_")
        assert result == "a" * 63

    def test_empty_result_returns_fallback(self):
        # A filename whose stem is all non-allowed chars (e.g. "!!!.mp4")
        result = make_safe_stem("!!!.mp4", "my_fallback")
        assert result == "my_fallback"

    def test_leading_trailing_underscores_trimmed(self):
        result = make_safe_stem(" lecture .mp4", "fallback")
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_allowed_chars_preserved(self):
        result = make_safe_stem("lecture-001_final.mp4", "fallback")
        assert result == "lecture-001_final"

    def test_path_traversal_deep(self):
        result = make_safe_stem("../../etc/passwd", "fallback")
        assert ".." not in result
        assert "/" not in result


# ---------------------------------------------------------------------------
# make_zip_filename
# ---------------------------------------------------------------------------


class TestMakeZipFilename:
    def test_format(self):
        ts = datetime(2026, 6, 4, 10, 30, 0)
        result = make_zip_filename("lecture", ts)
        assert result == "llm_analysis_package_lecture_20260604_103000.zip"

    def test_starts_with_prefix(self):
        result = make_zip_filename("my_stem", _FIXED_TS)
        assert result.startswith("llm_analysis_package_")

    def test_ends_with_zip(self):
        result = make_zip_filename("stem", _FIXED_TS)
        assert result.endswith(".zip")

    def test_matches_pattern(self):
        result = make_zip_filename("stem", _FIXED_TS)
        pattern = r"^llm_analysis_package_[^_].*_\d{8}_\d{6}\.zip$"
        assert re.match(pattern, result), f"Pattern mismatch: {result}"


# ---------------------------------------------------------------------------
# build_zip
# ---------------------------------------------------------------------------


class TestBuildZip:
    def test_creates_zip_file(self, tmp_path):
        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            **_MINIMAL_ZIP_KWARGS,
        )
        assert zip_path.exists()
        assert zip_path.suffix == ".zip"

    def test_returns_absolute_path(self, tmp_path):
        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            **_MINIMAL_ZIP_KWARGS,
        )
        assert zip_path.is_absolute()

    def test_all_required_entries_present(self, tmp_path):
        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            **_MINIMAL_ZIP_KWARGS,
        )
        required_entries = {
            "transcript_full_global_timecodes.md",
            "transcript_full_global_timecodes.json",
            "lecture_summary_input.md",
            "screenshots_manifest.csv",
            "failed_or_silent_parts.md",
            "metadata.json",
        }
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert required_entries.issubset(names)
        assert "screenshots/" in names

    def test_screenshot_directory_with_correct_files(self, tmp_path):
        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            screenshot_count=4,
            transcript_md="# Transcript",
            transcript_json="[]",
            summary_input_md="# Summary",
            screenshots_manifest_csv="header",
            failed_parts_md="# Failed",
            metadata_json="{}",
        )
        expected_screenshots = {
            "screenshots/frame_000000s.jpg",
            "screenshots/frame_000020s.jpg",
            "screenshots/frame_000040s.jpg",
            "screenshots/frame_000060s.jpg",
        }
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert "screenshots/" in names
        assert expected_screenshots.issubset(names)

    def test_screenshot_count_zero_has_directory_but_no_files(self, tmp_path):
        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            **_MINIMAL_ZIP_KWARGS,
        )
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        screenshot_files = [n for n in names if n.startswith("screenshots/") and n != "screenshots/"]
        assert "screenshots/" in names
        assert len(screenshot_files) == 0

    def test_screenshot_placeholder_is_valid_jpeg(self, tmp_path):
        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="lecture",
            ts=_FIXED_TS,
            screenshot_count=1,
            transcript_md="# Transcript",
            transcript_json="[]",
            summary_input_md="# Summary",
            screenshots_manifest_csv="header",
            failed_parts_md="# Failed",
            metadata_json="{}",
        )
        with zipfile.ZipFile(zip_path) as zf:
            data = zf.read("screenshots/frame_000000s.jpg")
        # JPEG magic bytes
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_zip_entry_names_are_fixed(self, tmp_path):
        """ZIP entries must not contain user-controlled values (path safety)."""
        zip_path = build_zip(
            output_dir=tmp_path,
            safe_stem="__injected__path",
            ts=_FIXED_TS,
            **_MINIMAL_ZIP_KWARGS,
        )
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        # None of the ZIP entry paths (not filename) should contain "injected"
        for name in names:
            assert "__injected__path" not in name


# ---------------------------------------------------------------------------
# Invariant: screenshot_count == manifest rows == screenshots/ files in ZIP
# ---------------------------------------------------------------------------


class TestScreenshotCountInvariant:
    def test_screenshot_count_invariant_from_pipeline(self, tmp_path, monkeypatch):
        """metadata.screenshot_count == manifest rows == files in screenshots/"""
        import app.pipeline.mock_pipeline as mp
        monkeypatch.setattr(mp.settings, "output_dir", str(tmp_path))

        import csv
        import io
        from unittest.mock import MagicMock
        from uuid import uuid4
        job = MagicMock()
        job.id = uuid4()
        job.original_filename = "lecture.mp4"
        job.stored_filename = f"{uuid4()}.mp4"
        job.input_path = "/uploads/test.mp4"
        job.video_size_bytes = None
        job.duration_seconds = None

        zip_path = mp.run_mock_output_pipeline(job)

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            metadata = json.loads(zf.read("metadata.json"))
            manifest_text = zf.read("screenshots_manifest.csv").decode()
            screenshot_files = [n for n in names if n.startswith("screenshots/") and n != "screenshots/"]

        manifest_rows = [r for r in csv.reader(io.StringIO(manifest_text))][1:]  # skip header

        assert metadata["screenshot_count"] == len(manifest_rows) == len(screenshot_files)
        assert metadata["screenshot_count"] == 4  # expected for MockTranscriber fixture (span 75s)


# ---------------------------------------------------------------------------
# run_mock_output_pipeline
# ---------------------------------------------------------------------------


class TestRunMockOutputPipeline:
    def test_success_returns_existing_zip(self, tmp_path, monkeypatch):
        import app.pipeline.mock_pipeline as mp

        monkeypatch.setattr(mp.settings, "output_dir", str(tmp_path))

        job = make_mock_job()
        zip_path = mp.run_mock_output_pipeline(job)

        assert zip_path.exists()
        assert zip_path.suffix == ".zip"

    def test_staging_dir_cleaned_up_on_success(self, tmp_path, monkeypatch):
        import app.pipeline.mock_pipeline as mp

        monkeypatch.setattr(mp.settings, "output_dir", str(tmp_path))

        job = make_mock_job()
        mp.run_mock_output_pipeline(job)

        staging = tmp_path / "staging" / str(job.id)
        assert not staging.exists()

    def test_staging_dir_cleaned_up_on_failure(self, tmp_path, monkeypatch):
        import app.pipeline.mock_pipeline as mp
        from app.pipeline import mock_pipeline

        monkeypatch.setattr(mp.settings, "output_dir", str(tmp_path))

        # Force failure inside the try block by patching classify_segments
        original_classify = mock_pipeline.classify_segments

        def boom(segments):
            raise RuntimeError("Simulated failure")

        monkeypatch.setattr(mock_pipeline, "classify_segments", boom)

        job = make_mock_job()
        with pytest.raises(RuntimeError, match="Simulated failure"):
            mp.run_mock_output_pipeline(job)

        staging = tmp_path / "staging" / str(job.id)
        assert not staging.exists()

    def test_zip_contains_required_entries(self, tmp_path, monkeypatch):
        import app.pipeline.mock_pipeline as mp

        monkeypatch.setattr(mp.settings, "output_dir", str(tmp_path))

        job = make_mock_job()
        zip_path = mp.run_mock_output_pipeline(job)

        required_entries = {
            "transcript_full_global_timecodes.md",
            "transcript_full_global_timecodes.json",
            "lecture_summary_input.md",
            "screenshots_manifest.csv",
            "failed_or_silent_parts.md",
            "metadata.json",
        }
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert required_entries.issubset(names)

    def test_zip_filename_format(self, tmp_path, monkeypatch):
        import app.pipeline.mock_pipeline as mp

        monkeypatch.setattr(mp.settings, "output_dir", str(tmp_path))

        job = make_mock_job()
        zip_path = mp.run_mock_output_pipeline(job)

        pattern = r"^llm_analysis_package_.*_\d{8}_\d{6}\.zip$"
        assert re.match(pattern, zip_path.name), f"Filename mismatch: {zip_path.name}"

    def test_output_dir_created_if_missing(self, tmp_path, monkeypatch):
        import app.pipeline.mock_pipeline as mp

        nested_output = tmp_path / "nested" / "output"
        monkeypatch.setattr(mp.settings, "output_dir", str(nested_output))

        job = make_mock_job()
        zip_path = mp.run_mock_output_pipeline(job)

        assert zip_path.exists()

    def test_metadata_has_correct_counts(self, tmp_path, monkeypatch):
        import app.pipeline.mock_pipeline as mp

        monkeypatch.setattr(mp.settings, "output_dir", str(tmp_path))

        job = make_mock_job()
        zip_path = mp.run_mock_output_pipeline(job)

        with zipfile.ZipFile(zip_path) as zf:
            metadata = json.loads(zf.read("metadata.json"))

        # MockTranscriber gives 5 segments, 1 silent
        assert metadata["total_segments"] == 5
        assert metadata["silent_segments"] == 1
