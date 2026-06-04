from app.pipeline.transcriber import TranscriptSegment

# Locked threshold — must not change. See CLAUDE.md key interfaces.
_NO_SPEECH_PROB_THRESHOLD = 0.6


def is_silent(segment: TranscriptSegment) -> bool:
    """Return True if the segment should be excluded from the main transcript.

    A segment is excluded (silent/failed) if ANY condition is true:
    - text.strip() == ""
    - no_speech_prob >= 0.6
    """
    return segment.text.strip() == "" or segment.no_speech_prob >= _NO_SPEECH_PROB_THRESHOLD


def classify_segments(
    segments: list[TranscriptSegment],
) -> tuple[list[TranscriptSegment], list[TranscriptSegment]]:
    """Split segments into active and silent lists.

    Returns:
        (active_segments, silent_segments)
    """
    active: list[TranscriptSegment] = []
    silent: list[TranscriptSegment] = []
    for seg in segments:
        if is_silent(seg):
            silent.append(seg)
        else:
            active.append(seg)
    return active, silent


def reason_for_exclusion(segment: TranscriptSegment) -> str:
    """Build a deterministic reason string describing why the segment is excluded.

    For the locked silent segment (no_speech_prob=0.85, empty text):
    returns exactly: "silent (no_speech_prob=0.85); empty text"

    Rules (applied in order, joined with "; "):
    1. If no_speech_prob >= 0.6 -> "silent (no_speech_prob=<value>)"
    2. If text.strip() == ""    -> "empty text"
    """
    reasons: list[str] = []
    if segment.no_speech_prob >= _NO_SPEECH_PROB_THRESHOLD:
        reasons.append(f"silent (no_speech_prob={segment.no_speech_prob:.2f})")
    if segment.text.strip() == "":
        reasons.append("empty text")
    return "; ".join(reasons)
