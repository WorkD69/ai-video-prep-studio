"""Guarded real-binary smoke tests for ffprobe/ffmpeg (Milestone 009).

Auto-skips when ffmpeg/ffprobe are absent (minimal CI). Runs locally and in
the Docker gate where the binaries exist. Uses a tiny synthetic testsrc video.
"""

import os
import shutil
import subprocess

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.pipeline.media import (  # noqa: E402
    FfmpegScreenshotExtractor,
    FfprobeMediaProber,
    screenshot_timestamps,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed — real-binary smoke runs in the Docker gate",
)


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory):
    """~65 s low-res 1 fps testsrc MP4 — small and fast to generate."""
    path = tmp_path_factory.mktemp("video") / "short.mp4"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=128x72:rate=1:duration=65",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        shell=False,
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    return path


def test_real_ffprobe_duration_about_65s(synthetic_video):
    duration = FfprobeMediaProber().probe_duration(str(synthetic_video))
    assert 63.0 <= duration <= 67.0


def test_real_extractor_produces_expected_frames(synthetic_video, tmp_path):
    out_dir = tmp_path / "shots"
    frames = FfmpegScreenshotExtractor().extract(
        str(synthetic_video), out_dir, [0, 20, 40, 60]
    )

    assert [p.name for p in frames] == [
        "frame_000000s.jpg",
        "frame_000020s.jpg",
        "frame_000040s.jpg",
        "frame_000060s.jpg",
    ]
    for frame in frames:
        data = frame.read_bytes()
        assert len(data) > 100  # real frame, not a stub
        assert data[:2] == b"\xff\xd8"


def test_real_probe_drives_timestamps_and_extraction(synthetic_video, tmp_path):
    """Full real flow: probe -> shared timestamps -> extract; counts match."""
    duration = FfprobeMediaProber().probe_duration(str(synthetic_video))
    timestamps = screenshot_timestamps(duration)
    frames = FfmpegScreenshotExtractor().extract(
        str(synthetic_video), tmp_path / "shots", timestamps
    )
    assert len(frames) == len(timestamps) == 4
