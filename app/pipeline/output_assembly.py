"""Output assembly for the LLM package v2 (ADR 006).

All functions are pure — no file I/O, no subprocess, no ffmpeg.
They return strings ready to be written into the ZIP package.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.pipeline.media import frame_filename, screenshot_timestamps
from app.pipeline.silence import reason_for_exclusion
from app.pipeline.timecodes import format_hhmmss, to_global_time
from app.pipeline.transcriber import TranscriptSegment

PACKAGE_VERSION = 2
NO_SPEECH_GAP_SECONDS = 30.0

_TRANSCRIPT_SOURCE_LABELS = {
    "manual_subtitles": "author's subtitles (accurate)",
    "mock": "mock transcript (test mode)",
}


@dataclass
class PackageInfo:
    """Everything the documents say about the video and how it was processed."""

    job_id: str
    title: str
    original_filename: str
    stored_filename: str
    source_type: str  # "file" | "url"
    duration_seconds: float
    processed_at: datetime
    transcript_source: str  # "manual_subtitles" | "whisper:<model>" | "mock"
    frames_mode: str  # "scene_change" | "fixed_20s" | "fake"
    source_url: str | None = None
    channel: str | None = None
    language: str | None = None
    chapters: list[dict] = field(default_factory=list)  # [{"start": float, "title": str}]
    subtitles_filename: str | None = None


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _transcript_source_label(source: str) -> str:
    if source.startswith("whisper:"):
        model = source.split(":", 1)[1]
        return f"automatic speech recognition (whisper {model}) — may contain recognition errors"
    return _TRANSCRIPT_SOURCE_LABELS.get(source, source)


def _frames_label(info: PackageInfo, frame_count: int) -> str:
    if info.frames_mode == "scene_change":
        return f"{frame_count} keyframes taken at visual scene changes"
    return f"{frame_count} frames, one every 20 seconds"


def _source_label(info: PackageInfo) -> str:
    return info.source_url if info.source_type == "url" and info.source_url else info.original_filename


def build_transcript_md(
    active_segments: list[TranscriptSegment],
    frame_seconds: list[int],
    info: PackageInfo,
    chunk_offset: float = 0.0,
) -> str:
    """Main document: chapters as headings, timecoded segments, and every frame
    inlined right after the segment whose time span contains it. Frames that
    fall into a pause go with the next segment; frames after the last speech
    are listed at the end.
    """
    segments = sorted(active_segments, key=lambda s: s.start)
    frames = sorted(set(frame_seconds))
    chapters = sorted(info.chapters, key=lambda c: c["start"])

    lines = [f"# {info.title}", ""]
    lines.append(f"- Source: {_source_label(info)}")
    if info.channel:
        lines.append(f"- Channel: {info.channel}")
    lines.append(f"- Duration: {format_hhmmss(info.duration_seconds)}")
    if info.language:
        lines.append(f"- Language: {info.language}")
    lines.append(f"- Transcript: {_transcript_source_label(info.transcript_source)}")
    lines.append(f"- Frames: {_frames_label(info, len(frames))}, inlined below as images")

    if chapters:
        lines += ["", "## Contents", ""]
        lines += [f"- [{format_hhmmss(c['start'])}] {c['title']}" for c in chapters]

    lines += ["", "---"]

    frame_index = 0
    chapter_index = 0

    def add_frames(until: float | None) -> None:
        nonlocal frame_index
        while frame_index < len(frames) and (until is None or frames[frame_index] < until):
            second = frames[frame_index]
            lines.append("")
            lines.append(f"![{format_hhmmss(second)}](frames/{frame_filename(second)})")
            frame_index += 1

    for seg in segments:
        g_start = to_global_time(seg.start, chunk_offset)
        g_end = to_global_time(seg.end, chunk_offset)
        while chapter_index < len(chapters) and chapters[chapter_index]["start"] <= g_start:
            chapter = chapters[chapter_index]
            lines += ["", f"## {format_hhmmss(chapter['start'])} — {chapter['title']}"]
            chapter_index += 1
        lines += ["", f"**[{format_hhmmss(g_start)}–{format_hhmmss(g_end)}]** {seg.text}"]
        add_frames(g_end)

    if frame_index < len(frames):
        lines += ["", "## Frames after the last speech" if segments else "## Frames"]
        add_frames(None)

    if not segments:
        lines += ["", "_No speech was recognised in this video._"]

    return "\n".join(lines) + "\n"


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


def build_screenshots_manifest_csv(
    all_segments: list[TranscriptSegment],
    duration_seconds: float | None = None,
    chunk_id: str = "chunk_001",
    timestamps: list[int] | None = None,
) -> str:
    """Build the frames manifest CSV (schema unchanged since M004).

    If timestamps is provided, it is used directly (the selected frame
    seconds — single source of truth with the files on disk). Otherwise span
    is determined by duration_seconds if provided, or by max end time of
    all_segments, and timestamps derive from screenshot_timestamps.
    """
    if timestamps is None:
        if duration_seconds is not None:
            span = duration_seconds
        elif all_segments:
            span = max(seg.end for seg in all_segments)
        else:
            span = 0.0
        timestamps = screenshot_timestamps(span)

    rows: list[str] = [
        "frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id"
    ]
    for frame_index, timestamp in enumerate(timestamps):
        filename = frame_filename(timestamp)
        rows.append(
            f"{frame_index},{filename},{timestamp},{format_hhmmss(float(timestamp))},{chunk_id}"
        )

    return "\n".join(rows)


def find_speech_gaps(
    active_segments: list[TranscriptSegment],
    duration_seconds: float,
    min_gap: float = NO_SPEECH_GAP_SECONDS,
) -> list[tuple[float, float]]:
    """Stretches of at least min_gap seconds without recognised speech,
    including the start and the end of the video."""
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for seg in sorted(active_segments, key=lambda s: s.start):
        if seg.start - cursor >= min_gap:
            gaps.append((cursor, seg.start))
        cursor = max(cursor, seg.end)
    if duration_seconds - cursor >= min_gap:
        gaps.append((cursor, duration_seconds))
    return gaps


def build_failed_parts_md(
    silent_segments: list[TranscriptSegment],
    speech_gaps: list[tuple[float, float]] | None = None,
    chunk_offset: float = 0.0,
) -> str:
    """Report of parts without usable speech: long pauses (music, silent demo)
    and segments whisper itself flagged as silent."""
    rows: list[tuple[float, float, str]] = []
    for start, end in speech_gaps or []:
        rows.append((start, end, f"no speech for {int(end - start)} s — check the frames"))
    for seg in silent_segments:
        rows.append((
            to_global_time(seg.start, chunk_offset),
            to_global_time(seg.end, chunk_offset),
            reason_for_exclusion(seg),
        ))
    rows.sort(key=lambda r: r[0])

    lines = [
        "# Failed or Silent Parts",
        "",
        "Parts of the video without usable speech. The transcript has nothing here;",
        "the frames are the only source for these parts.",
        "",
        "| Global Start | Global End | Reason |",
        "|---|---|---|",
    ]
    for start, end, reason in rows:
        lines.append(f"| {format_hhmmss(start)} | {format_hhmmss(end)} | {reason} |")
    if not rows:
        lines.append("| — | — | none |")
    return "\n".join(lines) + "\n"


def build_readme_md(info: PackageInfo, frame_count: int, has_failed_parts: bool) -> str:
    """Instructions for the agent that receives the package."""
    files = [
        "- `transcript.md` — **start here.** The whole video in time order: chapter headings,"
        " speech with `[HH:MM:SS–HH:MM:SS]` timecodes, and keyframes inserted as images right"
        " after the speech they accompany.",
        f"- `frames/` — {frame_count} keyframes named `frame_NNNNNNs.jpg`, where NNNNNN is the"
        " second of the video the frame was taken at.",
        "- `frames_manifest.csv` — the frame list with timestamps.",
        "- `transcript.json` — the same transcript as structured data.",
        "- `failed_parts.md` — stretches without speech (music, silent demos): only the frames"
        " show what happens there."
        + ("" if has_failed_parts else " (none in this video)"),
        "- `metadata.json` — technical details of the processing.",
    ]
    if info.subtitles_filename:
        files.append(f"- `{info.subtitles_filename}` — the original subtitle file.")

    caveats = [
        "- Frames are sampled at visual changes, not every second: short moments can be missing."
        if info.frames_mode == "scene_change"
        else "- Frames are taken every 20 seconds: short moments can be missing.",
        "- On-screen text (slides, code, UI) exists only in the images — read them when the"
        " speaker refers to the screen.",
    ]
    if info.transcript_source.startswith("whisper:"):
        caveats.append(
            "- The transcript is automatic speech recognition: names and technical terms can be"
            " misspelled (for example transliterated); trust the frames for exact spelling."
        )

    lines = [
        "# Video package for analysis",
        "",
        "This archive is a video converted into text and images so that an AI agent can"
        " \"watch\" it. Everything needed is inside; no access to the original video is required.",
        "",
        "## Video",
        "",
        f"- Title: {info.title}",
        f"- Source: {_source_label(info)}",
        f"- Duration: {format_hhmmss(info.duration_seconds)}",
        f"- Language: {info.language or 'unknown'}",
        f"- Transcript: {_transcript_source_label(info.transcript_source)}",
        f"- Frames: {_frames_label(info, frame_count)}",
        "",
        "## Files",
        "",
        *files,
        "",
        "## How to read it",
        "",
        "1. Read `transcript.md` from beginning to end before answering — do not sample it.",
        "2. Open the images where the speaker points at the screen or where the text alone is"
        " ambiguous.",
        "3. Cite timecodes like `[00:14:33]` so every claim can be checked in the video.",
        "",
        "## Caveats",
        "",
        *caveats,
        "",
    ]
    return "\n".join(lines)


def build_metadata_json(
    info: PackageInfo,
    all_segments: list[TranscriptSegment],
    silent_segments: list[TranscriptSegment],
    frame_count: int,
    safe_stem: str,
) -> str:
    """Build the job metadata JSON string."""
    data = {
        "package_version": PACKAGE_VERSION,
        "job_id": info.job_id,
        "title": info.title,
        "source_type": info.source_type,
        "source_url": info.source_url,
        "channel": info.channel,
        "original_filename": info.original_filename,
        "safe_filename": info.stored_filename,
        "safe_stem": safe_stem,
        "duration_seconds": info.duration_seconds,
        "language": info.language,
        "transcript_source": info.transcript_source,
        "frames_mode": info.frames_mode,
        "frame_count": frame_count,
        "total_segments": len(all_segments),
        "silent_segments": len(silent_segments),
        "chapters": info.chapters,
        "subtitles_file": info.subtitles_filename,
        "processed_at": _utc(info.processed_at),
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
