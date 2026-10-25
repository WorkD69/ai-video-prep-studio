"""Unit tests for frame selection (ADR 006): app/pipeline/frames.py + frames_crv.py.

crv is replaced by fake modules in sys.modules — no ffmpeg, no real crv.
The canary at the bottom runs only where the real crv is installed (Docker).
"""

import importlib.util
import inspect
import json
import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.pipeline.frames import (  # noqa: E402
    FakeFrameSelector,
    FixedIntervalFrameSelector,
    Frame,
    frame_budget,
    get_frame_selectors,
    select_frames,
)
from app.pipeline.media import FakeScreenshotExtractor, MediaError  # noqa: E402

JPEG = b"\xff\xd8\xff\xd9"


# ---------------------------------------------------------------------------
# fake crv
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_crv(monkeypatch):
    """Fake claude_real_video package; frames to emit are set via state["frames"]."""
    state = {"frames": [(0.0, "frame_001.jpg"), (12.4, "frame_002.jpg")], "calls": []}

    temporal_check = types.ModuleType("claude_real_video.temporal_check")
    temporal_check.temporal_hint = lambda *a, **k: "ORIGINAL HINT"

    core = types.ModuleType("claude_real_video.core")

    def original_fetch(src, out_dir, **kwargs):
        return "ORIGINAL FETCH"

    core.fetch_video = original_fetch

    def process(src, out_dir, **kwargs):
        # what crv would observe while running under our patches
        state["calls"].append({
            "src": src,
            "kwargs": kwargs,
            "fetch": core.fetch_video(src, out_dir),
            "hint": temporal_check.temporal_hint(src),
        })
        if state.get("raise"):
            raise RuntimeError("crv exploded")
        out = Path(out_dir)
        assert not out.exists(), "crv requires a fresh output dir"
        frames_dir = out / "frames"
        frames_dir.mkdir(parents=True)
        for _, name in state["frames"]:
            (frames_dir / name).write_bytes(JPEG)
        frames_json = out / "frames.json"
        frames_json.write_text(json.dumps({"frames": [
            {"file": name, "timestamp_sec": ts} for ts, name in state["frames"]
        ]}), encoding="utf-8")
        return types.SimpleNamespace(
            frames_dir=str(frames_dir),
            frames_json_path=None if state.get("no_json") else str(frames_json),
        )

    core.process = process
    package = types.ModuleType("claude_real_video")
    package.core = core
    package.temporal_check = temporal_check
    monkeypatch.setitem(sys.modules, "claude_real_video", package)
    monkeypatch.setitem(sys.modules, "claude_real_video.core", core)
    monkeypatch.setitem(sys.modules, "claude_real_video.temporal_check", temporal_check)
    state["core"], state["temporal_check"], state["original_fetch"] = core, temporal_check, original_fetch
    return state


def _select(tmp_path, duration=120.0):
    from app.pipeline.frames_crv import CrvFrameSelector

    out_dir = tmp_path / "staging" / "screenshots"
    return CrvFrameSelector().select("/uploads/v.mp4", out_dir, duration), out_dir


class TestCrvFrameSelector:
    def test_renames_to_second_based_names(self, fake_crv, tmp_path):
        frames, out_dir = _select(tmp_path)
        assert [f.second for f in frames] == [0, 12]
        assert [f.path.name for f in frames] == ["frame_000000s.jpg", "frame_000012s.jpg"]
        assert sorted(p.name for p in out_dir.iterdir()) == ["frame_000000s.jpg", "frame_000012s.jpg"]

    def test_patches_active_during_call_and_restored(self, fake_crv, tmp_path):
        _select(tmp_path)
        call = fake_crv["calls"][0]
        assert call["fetch"] == "/uploads/v.mp4"  # no copy of the video
        assert call["hint"] is None  # temporal pass skipped
        assert fake_crv["core"].fetch_video is fake_crv["original_fetch"]
        assert fake_crv["temporal_check"].temporal_hint("x") == "ORIGINAL HINT"

    def test_patches_restored_on_failure(self, fake_crv, tmp_path):
        fake_crv["raise"] = True
        with pytest.raises(RuntimeError):
            _select(tmp_path)
        assert fake_crv["core"].fetch_video is fake_crv["original_fetch"]

    def test_process_options(self, fake_crv, tmp_path):
        _select(tmp_path, duration=600.0)
        kwargs = fake_crv["calls"][0]["kwargs"]
        assert kwargs["do_transcribe"] is False
        assert kwargs["frame_width"] == 1280
        assert kwargs["dedup_threshold"] == 15
        assert kwargs["max_frames"] == frame_budget(600.0)

    def test_same_second_collision_keeps_first(self, fake_crv, tmp_path):
        fake_crv["frames"] = [(5.9, "frame_002.jpg"), (5.1, "frame_001.jpg"), (9.0, "frame_003.jpg")]
        frames, _ = _select(tmp_path)
        assert [f.second for f in frames] == [5, 9]

    def test_missing_frames_json_raises(self, fake_crv, tmp_path):
        fake_crv["no_json"] = True
        with pytest.raises(MediaError):
            _select(tmp_path)

    def test_no_frames_raises(self, fake_crv, tmp_path):
        fake_crv["frames"] = []
        with pytest.raises(MediaError):
            _select(tmp_path)

    def test_work_dir_removed(self, fake_crv, tmp_path):
        _, out_dir = _select(tmp_path)
        assert [p.name for p in out_dir.parent.iterdir()] == ["screenshots"]


# ---------------------------------------------------------------------------
# budget, fallback, fake, backend selection
# ---------------------------------------------------------------------------


class TestFrameBudget:
    def test_five_per_minute(self):
        assert frame_budget(3600) == 300
        assert frame_budget(30 * 60) == 150

    def test_clamped(self):
        assert frame_budget(10) == 20
        assert frame_budget(3 * 3600) == 300


class _Exploding:
    mode = "scene_change"

    def select(self, input_path, out_dir, duration):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "partial.jpg").write_bytes(JPEG)
        raise RuntimeError("boom")


class TestSelectFrames:
    def test_primary_ok(self, tmp_path):
        frames, mode = select_frames(FakeFrameSelector(), None, "/v.mp4", tmp_path / "s", 45.0)
        assert mode == "fake"
        assert [f.second for f in frames] == [0, 20, 40]

    def test_fallback_on_failure_cleans_partial_output(self, tmp_path):
        fallback = FixedIntervalFrameSelector(FakeScreenshotExtractor())
        frames, mode = select_frames(_Exploding(), fallback, "/v.mp4", tmp_path / "s", 45.0)
        assert mode == "fixed_20s"
        assert sorted(p.name for p in (tmp_path / "s").iterdir()) == [
            "frame_000000s.jpg", "frame_000020s.jpg", "frame_000040s.jpg"]

    def test_no_fallback_propagates(self, tmp_path):
        with pytest.raises(RuntimeError):
            select_frames(_Exploding(), None, "/v.mp4", tmp_path / "s", 45.0)


class TestGetFrameSelectors:
    def test_fake(self, monkeypatch):
        import app.pipeline.frames as frames

        monkeypatch.setattr(frames.settings, "frame_backend", "fake")
        primary, fallback = get_frame_selectors()
        assert isinstance(primary, FakeFrameSelector) and fallback is None

    def test_crv_with_ffmpeg_fallback(self, monkeypatch):
        import app.pipeline.frames as frames
        from app.pipeline.frames_crv import CrvFrameSelector

        monkeypatch.setattr(frames.settings, "frame_backend", "crv")
        primary, fallback = get_frame_selectors()
        assert isinstance(primary, CrvFrameSelector)
        assert fallback.mode == "fixed_20s"


def test_frame_record():
    assert Frame(second=3, path=Path("x")).second == 3


# ---------------------------------------------------------------------------
# canary: the crv internals we patch still exist (runs where crv is installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("claude_real_video") is None,
    reason="claude-real-video not installed — canary runs in the Docker gate",
)
def test_crv_internals_canary():
    from claude_real_video import core, temporal_check

    assert callable(core.fetch_video)
    assert callable(temporal_check.temporal_hint)
    params = inspect.signature(core.process).parameters
    for name in ("do_transcribe", "frame_width", "dedup_threshold", "max_frames"):
        assert name in params
    source = inspect.getsource(core.process)
    assert "fetch_video(" in source
    assert "from .temporal_check import temporal_hint" in source
