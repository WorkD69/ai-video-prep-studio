"""Frame selection interface (ADR 006).

FrameSelector mirrors the Transcriber / MediaProber pattern: a real
scene-change selector (CrvFrameSelector, app/pipeline/frames_crv.py), a
fixed-interval fallback built on the M009 ffmpeg extractor, and a
deterministic fake for CI — selected by settings.frame_backend.

Every selector writes frame_{sec:06d}s.jpg files into out_dir and returns
them as Frame records sorted by second.
"""

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import structlog

from app.config import settings
from app.pipeline.media import (
    FakeScreenshotExtractor,
    FfmpegScreenshotExtractor,
    ScreenshotExtractor,
    screenshot_timestamps,
)

logger = structlog.get_logger()


@dataclass(frozen=True)
class Frame:
    second: int
    path: Path


class FrameSelector(Protocol):
    mode: str

    def select(self, input_path: str, out_dir: Path, duration: float) -> list[Frame]: ...


def frame_budget(duration: float) -> int:
    """~frames_per_minute, clamped to [min_frames, max_frames] (ADR 006)."""
    wanted = math.ceil(duration / 60 * settings.frames_per_minute)
    return min(settings.max_frames, max(settings.min_frames, wanted))


def frame_filename(second: int) -> str:
    return f"frame_{second:06d}s.jpg"


class FixedIntervalFrameSelector:
    """One frame every 20 s via a ScreenshotExtractor (the M009 behaviour)."""

    mode = "fixed_20s"

    def __init__(self, extractor: ScreenshotExtractor):
        self._extractor = extractor

    def select(self, input_path: str, out_dir: Path, duration: float) -> list[Frame]:
        timestamps = screenshot_timestamps(duration)
        paths = self._extractor.extract(input_path, out_dir, timestamps)
        return [Frame(second=sec, path=path) for sec, path in zip(timestamps, sorted(paths))]


class FakeFrameSelector(FixedIntervalFrameSelector):
    """Deterministic placeholder frames every 20 s — no ffmpeg, no crv."""

    mode = "fake"

    def __init__(self):
        super().__init__(FakeScreenshotExtractor())


def select_frames(
    primary: FrameSelector,
    fallback: FrameSelector | None,
    input_path: str,
    out_dir: Path,
    duration: float,
) -> tuple[list[Frame], str]:
    """Run the primary selector; on any failure fall back (if configured).

    Returns (frames, mode). Without a fallback the primary error propagates.
    """
    try:
        return primary.select(input_path, out_dir, duration), primary.mode
    except Exception as exc:
        if fallback is None:
            raise
        logger.warning(
            "frames_primary_failed_using_fallback",
            stage="frames",
            mode=primary.mode,
            error_type=type(exc).__name__,
        )
        shutil.rmtree(out_dir, ignore_errors=True)
        return fallback.select(input_path, out_dir, duration), fallback.mode


def get_frame_selectors() -> tuple[FrameSelector, FrameSelector | None]:
    """Return (primary, fallback) selected by settings.frame_backend."""
    if settings.frame_backend == "fake":
        return FakeFrameSelector(), None
    from app.pipeline.frames_crv import CrvFrameSelector

    return CrvFrameSelector(), FixedIntervalFrameSelector(FfmpegScreenshotExtractor())
