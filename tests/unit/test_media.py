"""Unit tests for app/pipeline/media.py (Milestone 009).

Fake-seam tests only: deterministic, no ffmpeg/ffprobe, no subprocess.
Covers screenshot_timestamps, fake implementations, MediaError codes,
and backend selection by settings.media_backend.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.pipeline.media import (  # noqa: E402
    FakeMediaProber,
    FakeScreenshotExtractor,
    FfmpegScreenshotExtractor,
    FfprobeMediaProber,
    MediaError,
    get_media_backend,
    get_media_prober,
    screenshot_timestamps,
)


# ---------------------------------------------------------------------------
# screenshot_timestamps — single source of truth
# ---------------------------------------------------------------------------


class TestScreenshotTimestamps:
    def test_span_75_gives_four_timestamps(self):
        assert screenshot_timestamps(75.0) == [0, 20, 40, 60]

    def test_span_3600_gives_180_entries(self):
        result = screenshot_timestamps(3600.0)
        assert len(result) == 180
        assert result[0] == 0
        assert result[-1] == 3580

    def test_span_20_gives_single_zero(self):
        assert screenshot_timestamps(20.0) == [0]

    def test_span_0_gives_empty(self):
        assert screenshot_timestamps(0.0) == []

    def test_short_video_19s_one_screenshot_at_zero(self):
        assert screenshot_timestamps(19.0) == [0]

    def test_short_video_15s_one_screenshot_at_zero(self):
        assert screenshot_timestamps(15.0) == [0]

    def test_short_video_5s_one_screenshot_at_zero(self):
        assert screenshot_timestamps(5.0) == [0]

    def test_span_just_over_boundary_includes_boundary(self):
        # 60.5 → 0,20,40,60
        assert screenshot_timestamps(60.5) == [0, 20, 40, 60]

    def test_span_exactly_on_boundary_excludes_boundary(self):
        # strict t < span: 60 → 0,20,40
        assert screenshot_timestamps(60.0) == [0, 20, 40]


# ---------------------------------------------------------------------------
# Manifest consumes the shared timestamps list
# ---------------------------------------------------------------------------


class TestManifestUsesSharedTimestamps:
    def test_manifest_rows_match_passed_timestamps(self):
        from app.pipeline.output_assembly import build_screenshots_manifest_csv

        result = build_screenshots_manifest_csv([], timestamps=[0, 20, 40])
        lines = result.splitlines()
        assert lines[0] == "frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id"
        data_rows = lines[1:]
        assert len(data_rows) == 3
        assert data_rows[0].split(",")[1] == "frame_000000s.jpg"
        assert data_rows[2].split(",")[2] == "40"

    def test_manifest_without_timestamps_keeps_m004_behaviour(self):
        from app.pipeline.output_assembly import build_screenshots_manifest_csv

        result = build_screenshots_manifest_csv([], duration_seconds=75.0)
        assert len(result.splitlines()[1:]) == 4


# ---------------------------------------------------------------------------
# FakeMediaProber
# ---------------------------------------------------------------------------


class TestFakeMediaProber:
    def test_returns_configured_duration(self):
        prober = FakeMediaProber(duration=1800.0)
        assert prober.probe_duration("/any/path.mp4") == 1800.0

    def test_default_duration_is_positive(self):
        prober = FakeMediaProber()
        assert prober.probe_duration("/any/path.mp4") > 0


# ---------------------------------------------------------------------------
# FakeScreenshotExtractor
# ---------------------------------------------------------------------------


class TestFakeScreenshotExtractor:
    def test_creates_exactly_requested_files(self, tmp_path):
        extractor = FakeScreenshotExtractor()
        out_dir = tmp_path / "screenshots"
        paths = extractor.extract("/any/path.mp4", out_dir, [0, 20, 40, 60])

        assert len(paths) == 4
        names = sorted(p.name for p in out_dir.iterdir())
        assert names == [
            "frame_000000s.jpg",
            "frame_000020s.jpg",
            "frame_000040s.jpg",
            "frame_000060s.jpg",
        ]

    def test_files_are_valid_jpeg(self, tmp_path):
        extractor = FakeScreenshotExtractor()
        out_dir = tmp_path / "screenshots"
        paths = extractor.extract("/any/path.mp4", out_dir, [0])

        data = paths[0].read_bytes()
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_returns_sorted_paths(self, tmp_path):
        extractor = FakeScreenshotExtractor()
        out_dir = tmp_path / "screenshots"
        paths = extractor.extract("/any/path.mp4", out_dir, [40, 0, 20])
        assert paths == sorted(paths)
        assert [p.name for p in paths] == [
            "frame_000000s.jpg",
            "frame_000020s.jpg",
            "frame_000040s.jpg",
        ]

    def test_empty_timestamps_creates_no_files(self, tmp_path):
        extractor = FakeScreenshotExtractor()
        out_dir = tmp_path / "screenshots"
        paths = extractor.extract("/any/path.mp4", out_dir, [])
        assert paths == []
        files = list(out_dir.iterdir()) if out_dir.exists() else []
        assert files == []


# ---------------------------------------------------------------------------
# MediaError — safe codes
# ---------------------------------------------------------------------------


class TestMediaError:
    def test_is_exception_with_safe_code(self):
        err = MediaError("invalid_or_unreadable_media")
        assert isinstance(err, Exception)
        assert str(err) == "invalid_or_unreadable_media"

    def test_extraction_failed_code(self):
        err = MediaError("screenshot_extraction_failed")
        assert str(err) == "screenshot_extraction_failed"


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class TestBackendSelection:
    def test_fake_backend_selected(self, monkeypatch):
        import app.pipeline.media as media

        monkeypatch.setattr(media.settings, "media_backend", "fake")
        prober, extractor = get_media_backend()
        assert isinstance(prober, FakeMediaProber)
        assert isinstance(extractor, FakeScreenshotExtractor)

    def test_ffmpeg_backend_selected(self, monkeypatch):
        import app.pipeline.media as media

        monkeypatch.setattr(media.settings, "media_backend", "ffmpeg")
        prober, extractor = get_media_backend()
        assert isinstance(prober, FfprobeMediaProber)
        assert isinstance(extractor, FfmpegScreenshotExtractor)

    def test_get_media_prober_fake(self, monkeypatch):
        import app.pipeline.media as media

        monkeypatch.setattr(media.settings, "media_backend", "fake")
        assert isinstance(get_media_prober(), FakeMediaProber)

    def test_get_media_prober_ffmpeg(self, monkeypatch):
        import app.pipeline.media as media

        monkeypatch.setattr(media.settings, "media_backend", "ffmpeg")
        assert isinstance(get_media_prober(), FfprobeMediaProber)


# ---------------------------------------------------------------------------
# FfprobeMediaProber — subprocess mocked, no real ffprobe
# ---------------------------------------------------------------------------


def _patch_run(monkeypatch, stdout=b"", returncode=0, side_effect=None):
    """Replace subprocess.run inside app.pipeline.media; capture the call."""
    import app.pipeline.media as media

    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        if side_effect is not None:
            raise side_effect
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = b""
        return result

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    return calls


class TestFfprobeMediaProber:
    def test_parses_float_duration(self, monkeypatch):
        _patch_run(monkeypatch, stdout=b"65.43\n")
        assert FfprobeMediaProber().probe_duration("/uploads/x.mp4") == 65.43

    def test_subprocess_call_is_safe(self, monkeypatch):
        calls = _patch_run(monkeypatch, stdout=b"10.0\n")
        FfprobeMediaProber().probe_duration("/uploads/x.mp4")

        cmd = calls["cmd"]
        kwargs = calls["kwargs"]
        assert isinstance(cmd, list)
        assert cmd[0] == "ffprobe"
        assert cmd[-1] == "/uploads/x.mp4"
        assert kwargs.get("shell", False) is False
        assert kwargs.get("capture_output") is True
        assert kwargs.get("timeout") is not None

    def test_nonzero_exit_raises_media_error(self, monkeypatch):
        _patch_run(monkeypatch, stdout=b"", returncode=1)
        with pytest.raises(MediaError, match="invalid_or_unreadable_media"):
            FfprobeMediaProber().probe_duration("/uploads/x.mp4")

    def test_empty_output_raises(self, monkeypatch):
        _patch_run(monkeypatch, stdout=b"")
        with pytest.raises(MediaError):
            FfprobeMediaProber().probe_duration("/uploads/x.mp4")

    def test_unparsable_output_raises(self, monkeypatch):
        _patch_run(monkeypatch, stdout=b"N/A\n")
        with pytest.raises(MediaError):
            FfprobeMediaProber().probe_duration("/uploads/x.mp4")

    @pytest.mark.parametrize("bad", [b"nan", b"inf", b"-inf", b"-5.0", b"0", b"0.0"])
    def test_nan_inf_nonpositive_raise(self, monkeypatch, bad):
        _patch_run(monkeypatch, stdout=bad)
        with pytest.raises(MediaError):
            FfprobeMediaProber().probe_duration("/uploads/x.mp4")

    def test_timeout_raises_media_error(self, monkeypatch):
        _patch_run(
            monkeypatch,
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
        )
        with pytest.raises(MediaError, match="invalid_or_unreadable_media"):
            FfprobeMediaProber().probe_duration("/uploads/x.mp4")

    def test_missing_binary_raises_media_error(self, monkeypatch):
        _patch_run(monkeypatch, side_effect=FileNotFoundError("ffprobe"))
        with pytest.raises(MediaError, match="invalid_or_unreadable_media"):
            FfprobeMediaProber().probe_duration("/uploads/x.mp4")


# ---------------------------------------------------------------------------
# FfmpegScreenshotExtractor — subprocess mocked, raw frames simulated
# ---------------------------------------------------------------------------


def _patch_ffmpeg_run(monkeypatch, num_raw_frames, returncode=0):
    """Fake ffmpeg: writes num_raw_frames raw_%06d.jpg files at the argv
    output pattern, exactly like single-pass fps=1/20 would."""
    import app.pipeline.media as media

    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        pattern = Path(cmd[-1])
        for k in range(1, num_raw_frames + 1):
            raw = pattern.parent / (pattern.name % k)
            raw.write_bytes(b"\xff\xd8" + f"FRAME{k:03d}".encode() + b"\xff\xd9")
        result = MagicMock()
        result.returncode = returncode
        result.stdout = b""
        result.stderr = b""
        return result

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    return calls


class TestFfmpegScreenshotExtractor:
    def test_renames_raw_frames_to_seconds(self, monkeypatch, tmp_path):
        _patch_ffmpeg_run(monkeypatch, num_raw_frames=3)
        out_dir = tmp_path / "shots"

        paths = FfmpegScreenshotExtractor().extract("/uploads/x.mp4", out_dir, [0, 20, 40])

        assert [p.name for p in paths] == [
            "frame_000000s.jpg",
            "frame_000020s.jpg",
            "frame_000040s.jpg",
        ]
        # raw frame k (1-based) maps to second (k-1)*20
        assert b"FRAME001" in (out_dir / "frame_000000s.jpg").read_bytes()
        assert b"FRAME003" in (out_dir / "frame_000040s.jpg").read_bytes()

    def test_extra_trailing_frame_dropped(self, monkeypatch, tmp_path):
        _patch_ffmpeg_run(monkeypatch, num_raw_frames=4)  # one extra
        out_dir = tmp_path / "shots"

        paths = FfmpegScreenshotExtractor().extract("/uploads/x.mp4", out_dir, [0, 20, 40])

        assert len(paths) == 3
        files = sorted(p.name for p in out_dir.glob("frame_*.jpg"))
        assert files == ["frame_000000s.jpg", "frame_000020s.jpg", "frame_000040s.jpg"]

    def test_missing_frame_raises(self, monkeypatch, tmp_path):
        _patch_ffmpeg_run(monkeypatch, num_raw_frames=2)  # one short
        out_dir = tmp_path / "shots"

        with pytest.raises(MediaError, match="screenshot_extraction_failed"):
            FfmpegScreenshotExtractor().extract("/uploads/x.mp4", out_dir, [0, 20, 40])

    def test_nonzero_exit_raises(self, monkeypatch, tmp_path):
        _patch_ffmpeg_run(monkeypatch, num_raw_frames=0, returncode=1)
        out_dir = tmp_path / "shots"

        with pytest.raises(MediaError, match="screenshot_extraction_failed"):
            FfmpegScreenshotExtractor().extract("/uploads/x.mp4", out_dir, [0])

    def test_empty_timestamps_skips_ffmpeg(self, monkeypatch, tmp_path):
        calls = _patch_ffmpeg_run(monkeypatch, num_raw_frames=0)
        out_dir = tmp_path / "shots"

        paths = FfmpegScreenshotExtractor().extract("/uploads/x.mp4", out_dir, [])

        assert paths == []
        assert "cmd" not in calls  # ffmpeg never invoked

    def test_subprocess_call_is_safe(self, monkeypatch, tmp_path):
        calls = _patch_ffmpeg_run(monkeypatch, num_raw_frames=1)
        out_dir = tmp_path / "shots"

        FfmpegScreenshotExtractor().extract("/uploads/x.mp4", out_dir, [0])

        cmd = calls["cmd"]
        kwargs = calls["kwargs"]
        assert isinstance(cmd, list)
        assert cmd[0] == "ffmpeg"
        # eof_action=pass: ffmpeg's default rounding drops the final 20 s slot
        # (65 s -> 3 frames); pass-through guarantees it, the reconciliation
        # drops any extra trailing frame. Confirmed by the real-binary smoke.
        assert "fps=1/20:eof_action=pass" in cmd
        assert "/uploads/x.mp4" in cmd
        assert kwargs.get("shell", False) is False
        assert kwargs.get("capture_output") is True
        assert kwargs.get("timeout") is not None

    def test_timeout_raises_media_error(self, monkeypatch, tmp_path):
        import app.pipeline.media as media

        def raising_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=600)

        monkeypatch.setattr(media.subprocess, "run", raising_run)

        with pytest.raises(MediaError, match="screenshot_extraction_failed"):
            FfmpegScreenshotExtractor().extract("/uploads/x.mp4", tmp_path / "shots", [0])
