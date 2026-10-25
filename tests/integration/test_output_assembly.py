"""Integration tests for app/pipeline/output_assembly.py — LLM package v2 (ADR 006).

Uses the locked MockTranscriber 5-segment fixture:
  - seg 0: (0.0-15.0)  active  "Hello and welcome to this lecture."
  - seg 1: (15.0-30.0) active  "Today we will cover the main topic."
  - seg 2: (30.0-45.0) SILENT  "" / no_speech_prob=0.85
  - seg 3: (45.0-60.0) active  "Let us continue with the next point."
  - seg 4: (60.0-75.0) active  "Thank you for watching."
"""

import json
import re
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.pipeline.output_assembly import (
    PackageInfo,
    build_failed_parts_md,
    build_metadata_json,
    build_readme_md,
    build_screenshots_manifest_csv,
    build_transcript_json,
    build_transcript_md,
    find_speech_gaps,
)
from app.pipeline.silence import classify_segments
from app.pipeline.transcriber import MockTranscriber, TranscriptSegment

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def all_segments() -> list[TranscriptSegment]:
    return MockTranscriber().transcribe("dummy.mp4")


@pytest.fixture()
def active_segments(all_segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    active, _ = classify_segments(all_segments)
    return active


@pytest.fixture()
def silent_segments(all_segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    _, silent = classify_segments(all_segments)
    return silent


@pytest.fixture()
def info() -> PackageInfo:
    return PackageInfo(
        job_id="job-1",
        title="Claude Code за 50 минут",
        original_filename="Claude Code за 50 минут",
        stored_filename="abc.mp4",
        source_type="url",
        source_url="https://www.youtube.com/watch?v=x",
        channel="Some Channel",
        duration_seconds=75.0,
        language="ru",
        chapters=[{"start": 0.0, "title": "Intro"}, {"start": 45.0, "title": "Next point"}],
        processed_at=datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc),
        transcript_source="whisper:turbo",
        frames_mode="scene_change",
    )


# ---------------------------------------------------------------------------
# build_transcript_md — frames inline, chapters
# ---------------------------------------------------------------------------


class TestBuildTranscriptMd:
    def test_header_block(self, active_segments, info):
        md = build_transcript_md(active_segments, [], info)
        assert md.startswith("# Claude Code за 50 минут\n")
        assert "- Source: https://www.youtube.com/watch?v=x" in md
        assert "- Duration: 00:01:15" in md
        assert "- Language: ru" in md
        assert "whisper turbo" in md

    def test_contents_and_chapter_headings(self, active_segments, info):
        md = build_transcript_md(active_segments, [], info)
        assert "## Contents" in md
        assert "- [00:00:45] Next point" in md
        assert md.index("## 00:00:00 — Intro") < md.index("Hello and welcome")
        assert md.index("Today we will cover") < md.index("## 00:00:45 — Next point")
        assert md.index("## 00:00:45 — Next point") < md.index("Let us continue")

    def test_segments_timecoded_and_silent_excluded(self, active_segments, info):
        md = build_transcript_md(active_segments, [], info)
        assert "**[00:00:00–00:00:15]** Hello and welcome to this lecture." in md
        assert len(re.findall(r"^\*\*\[", md, re.M)) == 4

    def test_frame_goes_after_the_segment_containing_it(self, active_segments, info):
        md = build_transcript_md(active_segments, [5, 20], info)
        hello = md.index("Hello and welcome")
        frame5 = md.index("![00:00:05](frames/frame_000005s.jpg)")
        today = md.index("Today we will cover")
        frame20 = md.index("![00:00:20](frames/frame_000020s.jpg)")
        assert hello < frame5 < today < frame20

    def test_frame_in_pause_goes_with_next_segment(self, active_segments, info):
        md = build_transcript_md(active_segments, [35], info)  # 30-45 is the silent gap
        assert md.index("Let us continue") < md.index("frame_000035s.jpg")
        assert md.index("frame_000035s.jpg") < md.index("Thank you for watching")

    def test_frames_after_last_speech(self, active_segments, info):
        info = replace(info, duration_seconds=120.0)
        md = build_transcript_md(active_segments, [100], info)
        assert md.index("## Frames after the last speech") < md.index("frame_000100s.jpg")

    def test_every_frame_once(self, active_segments, info):
        frames = [0, 5, 20, 35, 60, 74]
        md = build_transcript_md(active_segments, frames + [20], info)
        assert len(re.findall(r"!\[\d\d:\d\d:\d\d\]\(frames/frame_\d{6}s\.jpg\)", md)) == len(frames)

    def test_no_speech_video(self, info):
        md = build_transcript_md([], [0, 20], info)
        assert "## Frames" in md
        assert "No speech was recognised" in md
        assert "frame_000020s.jpg" in md

    def test_no_chapters_no_contents(self, active_segments, info):
        md = build_transcript_md(active_segments, [], replace(info, chapters=[]))
        assert "## Contents" not in md

    def test_file_source_shows_filename(self, active_segments, info):
        file_info = replace(info, source_type="file", source_url=None, original_filename="lecture.mp4")
        assert "- Source: lecture.mp4" in build_transcript_md(active_segments, [], file_info)


# ---------------------------------------------------------------------------
# build_transcript_json (unchanged contract)
# ---------------------------------------------------------------------------


class TestBuildTranscriptJson:
    def test_entries(self, active_segments):
        data = json.loads(build_transcript_json(active_segments))
        assert len(data) == 4
        assert data[0] == {
            "start": 0.0, "end": 15.0, "start_formatted": "00:00:00",
            "end_formatted": "00:00:15", "text": "Hello and welcome to this lecture.",
        }


# ---------------------------------------------------------------------------
# frames manifest (schema unchanged since M004)
# ---------------------------------------------------------------------------


class TestFramesManifestCsv:
    def test_header_and_rows_from_timestamps(self):
        csv_text = build_screenshots_manifest_csv([], timestamps=[0, 7, 125])
        lines = csv_text.splitlines()
        assert lines[0] == "frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id"
        assert lines[1:] == [
            "0,frame_000000s.jpg,0,00:00:00,chunk_001",
            "1,frame_000007s.jpg,7,00:00:07,chunk_001",
            "2,frame_000125s.jpg,125,00:02:05,chunk_001",
        ]

    def test_span_fallback_every_20s(self, all_segments):
        assert len(build_screenshots_manifest_csv(all_segments).splitlines()) == 5


# ---------------------------------------------------------------------------
# speech gaps + failed parts
# ---------------------------------------------------------------------------


class TestFindSpeechGaps:
    def test_gap_between_segments_start_and_end(self):
        segs = [
            TranscriptSegment(40.0, 50.0, "a", 0.0),
            TranscriptSegment(51.0, 60.0, "b", 0.0),
            TranscriptSegment(100.0, 110.0, "c", 0.0),
        ]
        assert find_speech_gaps(segs, 200.0) == [(0.0, 40.0), (60.0, 100.0), (110.0, 200.0)]

    def test_short_gaps_ignored(self, active_segments):
        assert find_speech_gaps(active_segments, 75.0) == []  # 30-45 is only 15 s

    def test_no_speech_at_all(self):
        assert find_speech_gaps([], 90.0) == [(0.0, 90.0)]


class TestBuildFailedPartsMd:
    def test_rows_sorted_with_reasons(self, silent_segments):
        md = build_failed_parts_md(silent_segments, [(100.0, 145.0)])
        assert "| 00:00:30 | 00:00:45 | silent (no_speech_prob=0.85); empty text |" in md
        assert "| 00:01:40 | 00:02:25 | no speech for 45 s — check the frames |" in md
        assert md.index("00:00:30") < md.index("00:01:40")

    def test_empty(self):
        assert "| — | — | none |" in build_failed_parts_md([], [])


# ---------------------------------------------------------------------------
# README for the agent
# ---------------------------------------------------------------------------


class TestBuildReadmeMd:
    def test_lists_files_and_instructions(self, info):
        md = build_readme_md(info, 42, has_failed_parts=True)
        for name in ("transcript.md", "frames/", "frames_manifest.csv", "transcript.json",
                     "failed_parts.md", "metadata.json"):
            assert f"`{name}`" in md
        assert "42 keyframes" in md
        assert "start here" in md
        assert "[00:14:33]" in md

    def test_whisper_caveat_only_for_asr(self, info):
        assert "speech recognition" in build_readme_md(info, 1, True)
        manual = replace(info, transcript_source="manual_subtitles", subtitles_filename="subtitles.ru.vtt")
        md = build_readme_md(manual, 1, True)
        assert "misspelled" not in md
        assert "`subtitles.ru.vtt`" in md
        assert "author's subtitles" in md

    def test_fixed_interval_wording(self, info):
        md = build_readme_md(replace(info, frames_mode="fixed_20s"), 4, False)
        assert "every 20 seconds" in md
        assert "(none in this video)" in md


# ---------------------------------------------------------------------------
# metadata.json v2
# ---------------------------------------------------------------------------


class TestBuildMetadataJson:
    def test_fields(self, all_segments, silent_segments, info):
        data = json.loads(build_metadata_json(info, all_segments, silent_segments, 9, "Claude_Code"))
        assert data["package_version"] == 2
        assert data["title"] == "Claude Code за 50 минут"
        assert data["source_type"] == "url"
        assert data["source_url"] == "https://www.youtube.com/watch?v=x"
        assert data["transcript_source"] == "whisper:turbo"
        assert data["frames_mode"] == "scene_change"
        assert data["frame_count"] == 9
        assert data["total_segments"] == 5
        assert data["silent_segments"] == 1
        assert data["chapters"][1] == {"start": 45.0, "title": "Next point"}
        assert data["processed_at"] == "2026-10-25T12:00:00Z"
        assert data["safe_stem"] == "Claude_Code"

    def test_unicode_kept(self, all_segments, silent_segments, info):
        raw = build_metadata_json(info, all_segments, silent_segments, 0, "x")
        assert "Claude Code за 50 минут" in raw
