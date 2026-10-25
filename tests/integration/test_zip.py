"""Integration tests for ZIP packaging — LLM package v2 (ADR 006).

Covers:
- make_safe_stem sanitization (path traversal, unicode, spaces, overlong)
- make_zip_filename format
- build_zip contents, frame invariant, subtitles, entry-name safety
"""

import re
import zipfile
from datetime import datetime, timezone

import pytest

from app.pipeline.zip_packaging import (
    build_zip,
    make_safe_stem,
    make_zip_filename,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FIXED_TS = datetime(2026, 6, 4, 10, 30, 0, tzinfo=timezone.utc)

_FRAME_BYTES = b"\xff\xd8\xff\xe0REAL_FRAME\xff\xd9"


def _make_frames(dir_path, seconds):
    dir_path.mkdir(parents=True, exist_ok=True)
    for sec in seconds:
        (dir_path / f"frame_{sec:06d}s.jpg").write_bytes(_FRAME_BYTES)
    return dir_path


def _zip_kwargs(frames_dir, frame_count):
    return dict(
        readme_md="# Readme",
        transcript_md="# Transcript",
        transcript_json="[]",
        frames_manifest_csv="frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id",
        failed_parts_md="# Failed",
        metadata_json="{}",
        frames_dir=frames_dir,
        frame_count=frame_count,
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

    def test_cyrillic_transliterated(self):
        assert make_safe_stem("Как за 50 минут освоить Claude.mp4", "fallback") == "Kak_za_50_minut_osvoit_Claude"

    def test_underscore_runs_collapsed(self):
        assert make_safe_stem("a  ++  b.mp4", "fallback") == "a_b"

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
    def test_creates_zip_with_v2_entries(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [0, 7, 125])
        zip_path = build_zip(tmp_path, "lecture", _FIXED_TS, **_zip_kwargs(frames, 3))
        assert zip_path.is_absolute() and zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert names == [
            "README.md", "transcript.md", "transcript.json", "frames_manifest.csv",
            "failed_parts.md", "metadata.json", "frames/",
            "frames/frame_000000s.jpg", "frames/frame_000007s.jpg", "frames/frame_000125s.jpg",
        ]

    def test_documents_utf8(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [])
        kwargs = _zip_kwargs(frames, 0)
        kwargs["transcript_md"] = "# Привет"
        zip_path = build_zip(tmp_path, "lecture", _FIXED_TS, **kwargs)
        with zipfile.ZipFile(zip_path) as zf:
            assert zf.read("transcript.md").decode("utf-8") == "# Привет"

    def test_frames_stored_uncompressed_with_real_bytes(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [20])
        zip_path = build_zip(tmp_path, "lecture", _FIXED_TS, **_zip_kwargs(frames, 1))
        with zipfile.ZipFile(zip_path) as zf:
            info = zf.getinfo("frames/frame_000020s.jpg")
            assert info.compress_type == zipfile.ZIP_STORED
            assert zf.read(info) == _FRAME_BYTES

    def test_frame_count_mismatch_raises_without_partial_zip(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [0, 20])
        with pytest.raises(ValueError, match="frame invariant"):
            build_zip(tmp_path, "lecture", _FIXED_TS, **_zip_kwargs(frames, 3))
        assert list(tmp_path.glob("*.zip")) == []

    def test_foreign_files_in_frames_dir_ignored(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [0])
        (frames / "notes.txt").write_text("x")
        (frames / "frame_1.jpg").write_bytes(b"x")
        zip_path = build_zip(tmp_path, "lecture", _FIXED_TS, **_zip_kwargs(frames, 1))
        with zipfile.ZipFile(zip_path) as zf:
            assert [n for n in zf.namelist() if n.startswith("frames/frame_")] == ["frames/frame_000000s.jpg"]

    def test_subtitles_added_at_root(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [0])
        subs = tmp_path / "subtitles.ru.vtt"
        subs.write_bytes(b"WEBVTT\n")
        zip_path = build_zip(tmp_path, "lecture", _FIXED_TS, subtitles_path=subs, **_zip_kwargs(frames, 1))
        with zipfile.ZipFile(zip_path) as zf:
            assert zf.read("subtitles.ru.vtt") == b"WEBVTT\n"

    def test_unexpected_subtitles_name_rejected(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [0])
        bad = tmp_path / "evil.sh"
        bad.write_text("x")
        with pytest.raises(ValueError, match="subtitles"):
            build_zip(tmp_path, "lecture", _FIXED_TS, subtitles_path=bad, **_zip_kwargs(frames, 1))

    def test_missing_output_dir_raises(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [])
        with pytest.raises(ValueError, match="output_dir"):
            build_zip(tmp_path / "nope", "lecture", _FIXED_TS, **_zip_kwargs(frames, 0))

    def test_zip_name(self, tmp_path):
        frames = _make_frames(tmp_path / "frames", [])
        zip_path = build_zip(tmp_path, "lecture", _FIXED_TS, **_zip_kwargs(frames, 0))
        assert zip_path.name == "llm_analysis_package_lecture_20260604_103000.zip"
