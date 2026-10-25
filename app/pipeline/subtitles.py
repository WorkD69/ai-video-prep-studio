"""WebVTT parsing for author-provided subtitles (ADR 006 transcript source #1).

Only manual subtitles reach this parser (auto-captions are never downloaded),
so cues are plain sentences; we still strip inline tags/timestamps and merge
consecutive duplicate cues defensively.
"""

import html
import re

from app.pipeline.transcriber import TranscriptSegment

_TIMING = re.compile(
    r"^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})"
)
_TAG = re.compile(r"<[^>]*>")


def _seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2])
    hours = int(parts[-3]) if len(parts) == 3 else 0
    return hours * 3600 + minutes * 60 + seconds


def parse_vtt(text: str) -> list[TranscriptSegment]:
    """Parse WebVTT into segments ordered by start time."""
    segments: list[TranscriptSegment] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [line for line in block.split("\n") if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if _TIMING.match(line)), None)
        if timing_index is None:
            continue  # WEBVTT header, NOTE, STYLE, REGION blocks
        match = _TIMING.match(lines[timing_index])
        start, end = _seconds(match.group(1)), _seconds(match.group(2))
        body = " ".join(_TAG.sub("", line) for line in lines[timing_index + 1:])
        body = " ".join(html.unescape(body).split())
        if not body or end <= start:
            continue
        if segments and segments[-1].text == body and start <= segments[-1].end + 0.05:
            segments[-1].end = max(segments[-1].end, end)
            continue
        segments.append(TranscriptSegment(start=start, end=end, text=body, no_speech_prob=0.0))
    segments.sort(key=lambda s: s.start)
    return segments
