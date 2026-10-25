"""Media processing interfaces and implementations (Milestone 009, ADR 005).

MediaProber / ScreenshotExtractor mirror the Transcriber pattern (ADR 003):
real ffprobe/ffmpeg implementations plus deterministic fakes for CI/tests,
selected by settings.media_backend ("ffmpeg" | "fake").

SECURITY (ADR 005): every subprocess call uses an explicit argument list with
shell=False, capture_output=True and a timeout. The input path is always a
trusted, validated, resolved path under upload_dir — never original_filename,
never any user-controlled string. ffprobe output is parsed numerically only.
"""

import math
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from app.config import settings

_SCREENSHOT_INTERVAL = 20  # seconds, locked
_FFPROBE_TIMEOUT_SECONDS = 30
_FFMPEG_TIMEOUT_SECONDS = 3600  # own cap (3 h video, fallback path); rq_job_timeout also bounds the worker

# Minimal valid 1x1 white JPEG — same placeholder bytes as zip_packaging
_PLACEHOLDER_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
)


class MediaError(Exception):
    """Controlled media-processing failure with a safe, user-facing message."""


def screenshot_timestamps(span: float) -> list[int]:
    """Return screenshot seconds: 0, 20, 40, ... while t < span.

    Single source of truth for both the manifest rows and the extractor
    timestamps — the invariant metadata.screenshot_count == manifest rows ==
    files in screenshots/ holds by construction.
    """
    timestamps: list[int] = []
    t = 0
    while t < span:
        timestamps.append(t)
        t += _SCREENSHOT_INTERVAL
    return timestamps


class MediaProber(Protocol):
    def probe_duration(self, input_path: str) -> float: ...


class ScreenshotExtractor(Protocol):
    def extract(self, input_path: str, out_dir: Path, timestamps: list[int]) -> list[Path]: ...


class FfprobeMediaProber:
    """Real duration probing via ffprobe (container metadata only, no decode)."""

    def probe_duration(self, input_path: str) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            input_path,
        ]
        try:
            proc = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                timeout=_FFPROBE_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError):
            raise MediaError("invalid_or_unreadable_media")

        if proc.returncode != 0:
            raise MediaError("invalid_or_unreadable_media")

        output = proc.stdout.decode("ascii", errors="replace").strip()
        try:
            duration = float(output)
        except ValueError:
            raise MediaError("invalid_or_unreadable_media")

        if not math.isfinite(duration) or duration <= 0:
            raise MediaError("invalid_or_unreadable_media")
        return duration


class FfmpegScreenshotExtractor:
    """Real single-pass fps=1/20 extraction, reconciled to the requested timestamps.

    ffmpeg writes raw_%06d.jpg where %06d is the 1-based frame INDEX, not
    seconds: raw frame k corresponds to second (k-1)*20. Frames are renamed to
    frame_{sec:06d}s.jpg; extra trailing frames are dropped, a missing
    requested frame raises MediaError.
    """

    def extract(self, input_path: str, out_dir: Path, timestamps: list[int]) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        if not timestamps:
            return []

        with tempfile.TemporaryDirectory(dir=out_dir) as raw_dir_str:
            raw_dir = Path(raw_dir_str)
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                input_path,
                # eof_action=pass: with the default eof rounding ffmpeg emits
                # round(duration/20) frames and silently drops the final slot
                # (e.g. 65 s -> 3 frames, no frame_000060s). pass-through always
                # delivers it; an extra trailing frame is dropped by the
                # reconciliation below. Confirmed by the real-binary smoke test.
                "-vf",
                f"fps=1/{_SCREENSHOT_INTERVAL}:eof_action=pass",
                "-q:v",
                "2",
                "-f",
                "image2",
                str(raw_dir / "raw_%06d.jpg"),
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    shell=False,
                    capture_output=True,
                    timeout=_FFMPEG_TIMEOUT_SECONDS,
                )
            except (subprocess.TimeoutExpired, OSError):
                raise MediaError("screenshot_extraction_failed")

            if proc.returncode != 0:
                raise MediaError("screenshot_extraction_failed")

            paths: list[Path] = []
            for sec in timestamps:
                raw_index = sec // _SCREENSHOT_INTERVAL + 1
                raw_file = raw_dir / f"raw_{raw_index:06d}.jpg"
                if not raw_file.exists():
                    raise MediaError("screenshot_extraction_failed")
                dest = out_dir / f"frame_{sec:06d}s.jpg"
                raw_file.replace(dest)
                paths.append(dest)
            # frames beyond the requested timestamps stay in raw_dir and are
            # removed with it — the extra-trailing-frame case from the spec

        return sorted(paths)


def extract_audio(input_path: str, wav_path: Path) -> Path:
    """Extract a 16 kHz mono WAV for whisper. Raises MediaError on failure."""
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        input_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(wav_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            timeout=_FFMPEG_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        raise MediaError("audio_extraction_failed")

    if proc.returncode != 0 or not wav_path.exists():
        raise MediaError("audio_extraction_failed")
    return wav_path


class FakeMediaProber:
    """Deterministic prober for CI/tests — returns the configured duration."""

    def __init__(self, duration: float = 75.0):
        self._duration = duration

    def probe_duration(self, input_path: str) -> float:
        return self._duration


class FakeScreenshotExtractor:
    """Deterministic extractor for CI/tests — writes placeholder JPEG bytes.

    Creates frame_{sec:06d}s.jpg for each requested timestamp. No ffmpeg.
    """

    def extract(self, input_path: str, out_dir: Path, timestamps: list[int]) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for sec in timestamps:
            frame_path = out_dir / f"frame_{sec:06d}s.jpg"
            frame_path.write_bytes(_PLACEHOLDER_JPEG)
            paths.append(frame_path)
        return sorted(paths)


def get_media_prober() -> MediaProber:
    """Return the prober selected by settings.media_backend."""
    if settings.media_backend == "fake":
        return FakeMediaProber()
    return FfprobeMediaProber()


def get_media_backend() -> tuple[MediaProber, ScreenshotExtractor]:
    """Return (prober, extractor) selected by settings.media_backend."""
    if settings.media_backend == "fake":
        return FakeMediaProber(), FakeScreenshotExtractor()
    return FfprobeMediaProber(), FfmpegScreenshotExtractor()
