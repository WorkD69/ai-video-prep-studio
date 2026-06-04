from dataclasses import dataclass
from typing import Protocol


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

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(start=0.0, end=15.0, text="Hello and welcome to this lecture.", no_speech_prob=0.05),
            TranscriptSegment(start=15.0, end=30.0, text="Today we will cover the main topic.", no_speech_prob=0.08),
            TranscriptSegment(start=30.0, end=45.0, text="", no_speech_prob=0.85),  # SILENT segment
            TranscriptSegment(start=45.0, end=60.0, text="Let us continue with the next point.", no_speech_prob=0.10),
            TranscriptSegment(start=60.0, end=75.0, text="Thank you for watching.", no_speech_prob=0.07),
        ]
