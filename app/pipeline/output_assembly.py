"""Output assembly functions for Milestone 004.

All functions are pure — no file I/O, no subprocess, no ffmpeg.
They return strings ready to be written into the ZIP package.
"""

import json
from datetime import datetime, timezone

from app.pipeline.silence import reason_for_exclusion
from app.pipeline.timecodes import format_hhmmss, to_global_time
from app.pipeline.transcriber import TranscriptSegment

_SCREENSHOT_INTERVAL = 20  # seconds, locked


def build_transcript_md(
    active_segments: list[TranscriptSegment],
    chunk_offset: float = 0.0,
) -> str:
    """Build the full transcript Markdown with global timecodes.

    Only active (non-silent) segments are included.
    Sections are sorted by global start time.
    """
    sorted_segments = sorted(active_segments, key=lambda s: s.start)
    lines: list[str] = ["# Transcript"]
    for seg in sorted_segments:
        g_start = to_global_time(seg.start, chunk_offset)
        g_end = to_global_time(seg.end, chunk_offset)
        header = f"## [{format_hhmmss(g_start)} - {format_hhmmss(g_end)}]"
        lines.append("")
        lines.append(header)
        lines.append(seg.text)
    return "\n".join(lines)


def build_transcript_json(
    active_segments: list[TranscriptSegment],
    chunk_offset: float = 0.0,
) -> str:
    """Build the full transcript as a JSON string.

    Returns a JSON array where each entry has:
    start, end, start_formatted, end_formatted, text.
    """
    sorted_segments = sorted(active_segments, key=lambda s: s.start)
    entries = []
    for seg in sorted_segments:
        g_start = to_global_time(seg.start, chunk_offset)
        g_end = to_global_time(seg.end, chunk_offset)
        entries.append(
            {
                "start": g_start,
                "end": g_end,
                "start_formatted": format_hhmmss(g_start),
                "end_formatted": format_hhmmss(g_end),
                "text": seg.text,
            }
        )
    return json.dumps(entries, ensure_ascii=False, indent=2)


def _transcript_body(
    active_segments: list[TranscriptSegment],
    chunk_offset: float,
) -> str:
    """Return transcript section lines without the top-level '# Transcript' header."""
    sorted_segments = sorted(active_segments, key=lambda s: s.start)
    lines: list[str] = []
    for seg in sorted_segments:
        g_start = to_global_time(seg.start, chunk_offset)
        g_end = to_global_time(seg.end, chunk_offset)
        header = f"## [{format_hhmmss(g_start)} - {format_hhmmss(g_end)}]"
        lines.append("")
        lines.append(header)
        lines.append(seg.text)
    return "\n".join(lines)


def build_summary_input_md(
    active_segments: list[TranscriptSegment],
    original_filename: str,
    duration_seconds: float | None,
    processed_at: datetime,
    chunk_offset: float = 0.0,
) -> str:
    """Build the lecture summary input Markdown document for LLM consumption."""
    if duration_seconds is None:
        duration_str = "unknown"
    else:
        duration_str = f"{int(duration_seconds)} seconds"

    # Format processed_at as ISO 8601 UTC with Z suffix
    utc_dt = processed_at.astimezone(timezone.utc)
    processed_str = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    transcript_body = _transcript_body(active_segments, chunk_offset)

    lines = [
        "# Lecture Summary Input",
        "",
        "## Video Information",
        f"- File: {original_filename}",
        f"- Duration: {duration_str}",
        f"- Processed: {processed_str}",
        "",
        "## Instructions for LLM",
        "Please analyze this lecture transcript and provide:",
        "1. A concise summary (3-5 paragraphs)",
        "2. Key concepts and definitions",
        "3. Main arguments or conclusions",
        "4. Questions this lecture answers",
        "",
        "## Full Transcript",
        transcript_body,
    ]
    return "\n".join(lines)


def build_screenshots_manifest_csv(
    all_segments: list[TranscriptSegment],
    duration_seconds: float | None = None,
    chunk_id: str = "chunk_001",
) -> str:
    """Build the screenshots manifest CSV.

    Screenshots are taken every 20 seconds starting at 0.
    Span is determined by duration_seconds if provided, otherwise by max end
    time of all_segments. Timestamps: 0, 20, 40, ... while timestamp < span.
    """
    if duration_seconds is not None:
        span = duration_seconds
    elif all_segments:
        span = max(seg.end for seg in all_segments)
    else:
        span = 0.0

    rows: list[str] = [
        "frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id"
    ]
    timestamp = 0
    frame_index = 0
    while timestamp < span:
        filename = f"frame_{timestamp:06d}s.jpg"
        rows.append(
            f"{frame_index},{filename},{timestamp},{format_hhmmss(float(timestamp))},{chunk_id}"
        )
        frame_index += 1
        timestamp += _SCREENSHOT_INTERVAL

    return "\n".join(rows)


def build_failed_parts_md(
    silent_segments: list[TranscriptSegment],
    chunk_offset: float = 0.0,
) -> str:
    """Build the failed/silent parts Markdown report."""
    lines = [
        "# Failed or Silent Parts",
        "",
        "These segments were excluded from the main transcript.",
        "",
        "| Global Start | Global End | Reason |",
        "|---|---|---|",
    ]
    for seg in silent_segments:
        g_start = format_hhmmss(to_global_time(seg.start, chunk_offset))
        g_end = format_hhmmss(to_global_time(seg.end, chunk_offset))
        reason = reason_for_exclusion(seg)
        lines.append(f"| {g_start} | {g_end} | {reason} |")
    return "\n".join(lines)


def build_metadata_json(
    job_id: str,
    original_filename: str,
    stored_filename: str,
    duration_seconds: float | None,
    processed_at: datetime,
    all_segments: list[TranscriptSegment],
    silent_segments: list[TranscriptSegment],
    screenshot_count: int,
    chunk_count: int = 1,
    transcriber: str = "MockTranscriber",
    model_size: str = "mock",
) -> str:
    """Build the job metadata JSON string."""
    utc_dt = processed_at.astimezone(timezone.utc)
    processed_str = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    data = {
        "job_id": job_id,
        "original_filename": original_filename,
        "safe_filename": stored_filename,
        "duration_seconds": duration_seconds,
        "processed_at": processed_str,
        "transcriber": transcriber,
        "model_size": model_size,
        "total_segments": len(all_segments),
        "silent_segments": len(silent_segments),
        "screenshot_count": screenshot_count,
        "chunk_count": chunk_count,
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
