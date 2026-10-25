from dataclasses import dataclass
from typing import Protocol

from app.config import settings

# Whisper's prompt window is ~224 tokens; hotwords share it with the decoder
# prefix, so keep the term list short enough to never crowd it out.
_MAX_HOTWORDS_CHARS = 400


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    no_speech_prob: float


class Transcriber(Protocol):
    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        ...


class MockTranscriber:
    """Returns the locked 5-segment fixture from ADR 003."""

    detected_language: str | None = None

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(start=0.0, end=15.0, text="Hello and welcome to this lecture.", no_speech_prob=0.05),
            TranscriptSegment(start=15.0, end=30.0, text="Today we will cover the main topic.", no_speech_prob=0.08),
            TranscriptSegment(start=30.0, end=45.0, text="", no_speech_prob=0.85),  # SILENT segment
            TranscriptSegment(start=45.0, end=60.0, text="Let us continue with the next point.", no_speech_prob=0.10),
            TranscriptSegment(start=60.0, end=75.0, text="Thank you for watching.", no_speech_prob=0.07),
        ]


class FasterWhisperTranscriber:
    """Local faster-whisper transcription (ADR 006), GPU when available.

    - vad_filter: Silero VAD drops music/silence before decoding, so whisper
      cannot hallucinate captions over them.
    - condition_on_previous_text=False: stops repetition loops seeded by an
      earlier bad line.
    - hotwords: domain terms re-applied on every 30 s window (initial_prompt
      would be dropped after the first window with conditioning disabled).

    faster_whisper is imported lazily: it lives in requirements-media.txt
    (Docker only); CI runs with TRANSCRIBER_BACKEND=mock.
    """

    def __init__(self, model: str, language: str | None = None, hotwords: str | None = None):
        self._model = model
        self._language = language
        self._hotwords = hotwords or None
        self.detected_language: str | None = None

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        from faster_whisper import WhisperModel

        model = WhisperModel(self._model, device="auto", compute_type="auto")
        segments, info = model.transcribe(
            audio_path,
            language=self._language,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            hotwords=self._hotwords,
        )
        result = [
            TranscriptSegment(
                start=round(seg.start, 3),
                end=round(seg.end, 3),
                text=seg.text.strip(),
                no_speech_prob=seg.no_speech_prob,
            )
            for seg in segments
        ]
        self.detected_language = info.language
        return result


def build_hotwords(*sources: str | None) -> str:
    """Merge comma/newline-separated term lists: dedupe (case-insensitive),
    keep order, cap the total length."""
    terms: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if not source:
            continue
        for raw in source.replace("\n", ",").split(","):
            term = " ".join(raw.split())
            if term and term.lower() not in seen:
                seen.add(term.lower())
                terms.append(term)

    result = ""
    for term in terms:
        candidate = f"{result}, {term}" if result else term
        if len(candidate) > _MAX_HOTWORDS_CHARS:
            break
        result = candidate
    return result


def get_transcriber(language: str | None = None, hotwords: str | None = None) -> Transcriber:
    """Return the transcriber selected by settings.transcriber_backend."""
    if settings.transcriber_backend == "mock":
        return MockTranscriber()
    return FasterWhisperTranscriber(settings.whisper_model, language=language, hotwords=hotwords)
