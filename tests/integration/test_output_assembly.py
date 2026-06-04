"""Integration tests for app/pipeline/output_assembly.py.

Uses the locked MockTranscriber 5-segment fixture:
  - seg 0: (0.0-15.0)  active  "Hello and welcome to this lecture."
  - seg 1: (15.0-30.0) active  "Today we will cover the main topic."
  - seg 2: (30.0-45.0) SILENT  "" / no_speech_prob=0.85
  - seg 3: (45.0-60.0) active  "Let us continue with the next point."
  - seg 4: (60.0-75.0) active  "Thank you for watching."
"""

import json
from datetime import datetime, timezone

import pytest

from app.pipeline.output_assembly import (
    build_failed_parts_md,
    build_metadata_json,
    build_screenshots_manifest_csv,
    build_summary_input_md,
    build_transcript_json,
    build_transcript_md,
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
def processed_at() -> datetime:
    return datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# build_transcript_md
# ---------------------------------------------------------------------------


class TestBuildTranscriptMd:
    def test_contains_header(self, active_segments):
        result = build_transcript_md(active_segments)
        assert result.startswith("# Transcript")

    def test_contains_all_four_active_texts(self, active_segments):
        result = build_transcript_md(active_segments)
        assert "Hello and welcome to this lecture." in result
        assert "Today we will cover the main topic." in result
        assert "Let us continue with the next point." in result
        assert "Thank you for watching." in result

    def test_excludes_silent_segment(self, active_segments):
        result = build_transcript_md(active_segments)
        # The silent segment has empty text; verify the 30-45s timecode block is absent
        assert "00:00:30 - 00:00:45" not in result

    def test_timecode_format(self, active_segments):
        result = build_transcript_md(active_segments)
        # First active segment
        assert "## [00:00:00 - 00:00:15]" in result
        assert "## [00:00:15 - 00:00:30]" in result
        assert "## [00:00:45 - 00:01:00]" in result
        assert "## [00:01:00 - 00:01:15]" in result

    def test_sorted_by_start_time(self, active_segments):
        result = build_transcript_md(active_segments)
        pos_first = result.index("00:00:00")
        pos_second = result.index("00:00:15")
        assert pos_first < pos_second

    def test_four_sections_present(self, active_segments):
        result = build_transcript_md(active_segments)
        assert result.count("## [") == 4


# ---------------------------------------------------------------------------
# build_transcript_json
# ---------------------------------------------------------------------------


class TestBuildTranscriptJson:
    def test_returns_valid_json(self, active_segments):
        result = build_transcript_json(active_segments)
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_four_entries(self, active_segments):
        parsed = json.loads(build_transcript_json(active_segments))
        assert len(parsed) == 4

    def test_entry_fields(self, active_segments):
        parsed = json.loads(build_transcript_json(active_segments))
        entry = parsed[0]
        assert "start" in entry
        assert "end" in entry
        assert "start_formatted" in entry
        assert "end_formatted" in entry
        assert "text" in entry

    def test_first_entry_values(self, active_segments):
        parsed = json.loads(build_transcript_json(active_segments))
        first = parsed[0]
        assert first["start"] == 0.0
        assert first["end"] == 15.0
        assert first["start_formatted"] == "00:00:00"
        assert first["end_formatted"] == "00:00:15"
        assert first["text"] == "Hello and welcome to this lecture."

    def test_no_silent_segment(self, active_segments):
        parsed = json.loads(build_transcript_json(active_segments))
        starts = [e["start"] for e in parsed]
        assert 30.0 not in starts


# ---------------------------------------------------------------------------
# build_summary_input_md
# ---------------------------------------------------------------------------


class TestBuildSummaryInputMd:
    def test_duration_none_shows_unknown(self, active_segments, processed_at):
        result = build_summary_input_md(
            active_segments,
            original_filename="lecture.mp4",
            duration_seconds=None,
            processed_at=processed_at,
        )
        assert "Duration: unknown" in result

    def test_duration_float_shows_seconds(self, active_segments, processed_at):
        result = build_summary_input_md(
            active_segments,
            original_filename="lecture.mp4",
            duration_seconds=75.0,
            processed_at=processed_at,
        )
        assert "Duration: 75 seconds" in result

    def test_contains_filename(self, active_segments, processed_at):
        result = build_summary_input_md(
            active_segments,
            original_filename="lecture.mp4",
            duration_seconds=None,
            processed_at=processed_at,
        )
        assert "File: lecture.mp4" in result

    def test_processed_at_utc_iso8601(self, active_segments, processed_at):
        result = build_summary_input_md(
            active_segments,
            original_filename="lecture.mp4",
            duration_seconds=None,
            processed_at=processed_at,
        )
        assert "Processed: 2026-06-03T12:00:00Z" in result

    def test_contains_llm_instructions(self, active_segments, processed_at):
        result = build_summary_input_md(
            active_segments,
            original_filename="lecture.mp4",
            duration_seconds=None,
            processed_at=processed_at,
        )
        assert "## Instructions for LLM" in result
        assert "1. A concise summary" in result

    def test_contains_full_transcript_section(self, active_segments, processed_at):
        result = build_summary_input_md(
            active_segments,
            original_filename="lecture.mp4",
            duration_seconds=None,
            processed_at=processed_at,
        )
        assert "## Full Transcript" in result
        assert "Hello and welcome to this lecture." in result

    def test_no_top_level_transcript_header(self, active_segments, processed_at):
        result = build_summary_input_md(
            active_segments,
            original_filename="lecture.mp4",
            duration_seconds=None,
            processed_at=processed_at,
        )
        # The "# Transcript" top-level header should NOT appear
        assert "\n# Transcript\n" not in result
        assert not result.startswith("# Transcript")


# ---------------------------------------------------------------------------
# build_screenshots_manifest_csv
# ---------------------------------------------------------------------------


class TestBuildScreenshotsManifestCsv:
    def test_header_row(self, active_segments):
        result = build_screenshots_manifest_csv(active_segments, duration_seconds=75.0)
        first_line = result.splitlines()[0]
        assert first_line == "frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id"

    def test_four_rows_for_75s_span(self, active_segments):
        result = build_screenshots_manifest_csv(active_segments, duration_seconds=75.0)
        # header + 4 data rows
        data_rows = result.splitlines()[1:]
        assert len(data_rows) == 4

    def test_timestamps_0_20_40_60(self, active_segments):
        result = build_screenshots_manifest_csv(active_segments, duration_seconds=75.0)
        lines = result.splitlines()[1:]
        timestamps = [int(line.split(",")[2]) for line in lines]
        assert timestamps == [0, 20, 40, 60]

    def test_filename_pattern(self, active_segments):
        result = build_screenshots_manifest_csv(active_segments, duration_seconds=75.0)
        lines = result.splitlines()[1:]
        assert lines[0].split(",")[1] == "frame_000000s.jpg"
        assert lines[1].split(",")[1] == "frame_000020s.jpg"
        assert lines[2].split(",")[1] == "frame_000040s.jpg"
        assert lines[3].split(",")[1] == "frame_000060s.jpg"

    def test_chunk_id_in_rows(self, active_segments):
        result = build_screenshots_manifest_csv(
            active_segments, duration_seconds=75.0, chunk_id="chunk_001"
        )
        lines = result.splitlines()[1:]
        for line in lines:
            assert line.endswith("chunk_001")

    def test_span_from_active_segments_when_no_duration(self, active_segments):
        # active_segments max end = 75.0 → 4 screenshots
        result = build_screenshots_manifest_csv(active_segments, duration_seconds=None)
        data_rows = result.splitlines()[1:]
        assert len(data_rows) == 4

    def test_empty_segments_no_duration_yields_header_only(self):
        result = build_screenshots_manifest_csv([], duration_seconds=None)
        lines = result.splitlines()
        assert len(lines) == 1  # header only


# ---------------------------------------------------------------------------
# build_failed_parts_md
# ---------------------------------------------------------------------------


class TestBuildFailedPartsMd:
    def test_header_present(self, silent_segments):
        result = build_failed_parts_md(silent_segments)
        assert "# Failed or Silent Parts" in result

    def test_table_header_present(self, silent_segments):
        result = build_failed_parts_md(silent_segments)
        assert "| Global Start | Global End | Reason |" in result

    def test_silent_segment_row(self, silent_segments):
        result = build_failed_parts_md(silent_segments)
        assert "| 00:00:30 | 00:00:45 |" in result

    def test_exact_reason_string(self, silent_segments):
        result = build_failed_parts_md(silent_segments)
        assert "silent (no_speech_prob=0.85); empty text" in result

    def test_empty_silent_list_still_has_table_header(self):
        result = build_failed_parts_md([])
        assert "| Global Start | Global End | Reason |" in result
        assert "|---|---|---|" in result


# ---------------------------------------------------------------------------
# build_metadata_json
# ---------------------------------------------------------------------------


class TestBuildMetadataJson:
    def test_returns_valid_json(self, all_segments, active_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
        )
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_required_fields_present(self, all_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
        )
        parsed = json.loads(result)
        required_fields = [
            "job_id",
            "original_filename",
            "safe_filename",
            "duration_seconds",
            "processed_at",
            "transcriber",
            "model_size",
            "total_segments",
            "silent_segments",
            "screenshot_count",
            "chunk_count",
        ]
        for field in required_fields:
            assert field in parsed, f"Missing field: {field}"

    def test_counts(self, all_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
        )
        parsed = json.loads(result)
        assert parsed["total_segments"] == 5
        assert parsed["silent_segments"] == 1
        assert parsed["screenshot_count"] == 4

    def test_transcriber_and_model_defaults(self, all_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
        )
        parsed = json.loads(result)
        assert parsed["transcriber"] == "MockTranscriber"
        assert parsed["model_size"] == "mock"

    def test_transcriber_and_model_custom(self, all_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
            transcriber="FasterWhisperTranscriber",
            model_size="small",
        )
        parsed = json.loads(result)
        assert parsed["transcriber"] == "FasterWhisperTranscriber"
        assert parsed["model_size"] == "small"

    def test_processed_at_utc_z_suffix(self, all_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
        )
        parsed = json.loads(result)
        assert parsed["processed_at"] == "2026-06-03T12:00:00Z"

    def test_duration_none(self, all_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
        )
        parsed = json.loads(result)
        assert parsed["duration_seconds"] is None

    def test_safe_filename(self, all_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
        )
        parsed = json.loads(result)
        assert parsed["safe_filename"] == "uuid-abc.mp4"

    def test_chunk_count_default(self, all_segments, silent_segments, processed_at):
        result = build_metadata_json(
            job_id="job-123",
            original_filename="lecture.mp4",
            stored_filename="uuid-abc.mp4",
            duration_seconds=None,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=4,
        )
        parsed = json.loads(result)
        assert parsed["chunk_count"] == 1
