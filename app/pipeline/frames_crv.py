"""Scene-change frame selection via claude-real-video (crv), ADR 006.

crv is pinned to the audited commit in requirements-media.txt and imported
lazily (Docker only). It is driven as a library with two patches, applied only
for the duration of the call and always restored:

1. temporal_check.temporal_hint -> None: crv runs an extra full-decode ffmpeg
   pass just to print a slow-motion hint (~1/3 of the runtime); we never use it.
2. core.fetch_video -> the input path: crv would otherwise copy the (up to
   5 GiB) video into its output dir.

tests/unit/test_frames_crv.py holds a canary that fails loudly if a crv
upgrade renames these internals.
"""

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import structlog

from app.config import settings
from app.pipeline.frames import Frame, frame_budget, frame_filename
from app.pipeline.media import MediaError

logger = structlog.get_logger()

_DEDUP_THRESHOLD = 15  # % of pixels that must change; crv default 8 kept near-duplicate slides


@contextmanager
def _patched_crv(core, temporal_check, input_path: str):
    original_fetch = core.fetch_video
    original_hint = temporal_check.temporal_hint
    core.fetch_video = lambda src, out_dir, **kwargs: input_path
    temporal_check.temporal_hint = lambda *args, **kwargs: None
    try:
        yield
    finally:
        core.fetch_video = original_fetch
        temporal_check.temporal_hint = original_hint


def _read_frames_json(path: Path) -> list[tuple[float, str]]:
    """Return [(timestamp_sec, file)] for frames that carry a timestamp."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        (float(item["timestamp_sec"]), item["file"])
        for item in data.get("frames", [])
        if item.get("timestamp_sec") is not None and item.get("file")
    ]


class CrvFrameSelector:
    mode = "scene_change"

    def select(self, input_path: str, out_dir: Path, duration: float) -> list[Frame]:
        from claude_real_video import core, temporal_check

        out_dir.mkdir(parents=True, exist_ok=True)
        work_root = Path(tempfile.mkdtemp(prefix="crv_", dir=out_dir.parent))
        work_dir = work_root / "out"  # crv refuses a non-empty output dir
        try:
            with _patched_crv(core, temporal_check, input_path):
                result = core.process(
                    input_path,
                    str(work_dir),
                    do_transcribe=False,
                    frame_width=settings.frame_width,
                    dedup_threshold=_DEDUP_THRESHOLD,
                    max_frames=frame_budget(duration),
                )
            if not result.frames_json_path:
                raise MediaError("screenshot_extraction_failed")

            frames_dir = Path(result.frames_dir)
            frames: list[Frame] = []
            taken: set[int] = set()
            for timestamp, name in sorted(_read_frames_json(Path(result.frames_json_path))):
                second = int(timestamp)
                source = frames_dir / Path(name).name
                if second in taken or not source.exists():
                    continue  # same-second collision: the first frame wins
                dest = out_dir / frame_filename(second)
                shutil.move(str(source), dest)
                taken.add(second)
                frames.append(Frame(second=second, path=dest))

            if not frames:
                raise MediaError("screenshot_extraction_failed")
            logger.info("frames_selected", stage="frames", mode=self.mode, count=len(frames))
            return frames
        finally:
            shutil.rmtree(work_root, ignore_errors=True)
