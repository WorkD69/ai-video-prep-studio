# ADR 003 — Transcription: faster-whisper + Transcriber Interface

**Status:** Accepted  
**Date:** 2026-05-27  
**Deciders:** Project founder (Artem)

---

## Context

The core value of this service is producing accurate, timestamped transcripts from video files. Transcription is the most compute-intensive step (minutes per video). Key requirements:

- Must produce word/segment-level timestamps (for global timecode mapping)
- Must identify silent or low-confidence segments (for `failed_or_silent_parts.md`)
- Must work without external API calls (privacy, cost, offline capability)
- Must be swappable in CI/tests without loading a multi-GB model
- Must handle multiple languages (videos may not be in English)

---

## Decision

**Use `faster-whisper` running locally, behind a `Transcriber` interface.**

---

## Interface Design

```python
class Transcriber(Protocol):
    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        ...

@dataclass
class TranscriptSegment:
    start: float       # seconds
    end: float         # seconds
    text: str
    no_speech_prob: float
```

**Implementations:**
- `FasterWhisperTranscriber` — production, uses `faster-whisper` library
- `MockTranscriber` — testing/CI, returns deterministic fixture data

The pipeline code depends only on `Transcriber`. The concrete implementation is injected at job creation time based on configuration (`TRANSCRIBER=mock` env var in CI).

---

## Silence Detection Rules

A segment is considered **failed or silent** if ANY of the following are true:
1. `text` is empty or whitespace-only
2. `no_speech_prob >= 0.6`
3. Transcription raised an exception for this chunk (marked as `transcription_failed`)

These segments are excluded from the main transcript and listed in `failed_or_silent_parts.md` with their global timecodes.

**Threshold rationale:** `0.6` is the faster-whisper default recommendation for filtering hallucinated speech in quiet segments. Lower threshold → more false positives (real speech marked silent). Higher threshold → more hallucinations in output.

---

## Rationale for faster-whisper

**1. Local execution — no API costs, no data leaves the server.**
Videos may contain sensitive or proprietary content. Local transcription is a privacy and cost guarantee.

**2. faster-whisper is significantly faster than openai-whisper.**
CTranslate2-based backend: 2–4× faster than original Whisper on CPU, better GPU utilization.

**3. Returns per-segment `no_speech_prob`.**
This is the key metric for silence detection. Original Whisper returns it; faster-whisper exposes it cleanly.

**4. Handles long audio via chunking.**
Built-in VAD (Voice Activity Detection) chunking reduces hallucinations on long silences.

**5. Supports all Whisper model sizes.**
`base` for fast CI smoke tests, `medium`/`large-v3` for production quality.

**Rejected alternatives:**
- OpenAI Whisper API: external API, per-minute cost, data leaves server — unacceptable
- `openai-whisper` (original): slower, same model quality, no practical advantage
- `whisperx`: more complex dependency tree, better for diarization (MVP3 consideration)
- Vosk: lower accuracy, no `no_speech_prob` equivalent

---

## MockTranscriber Fixture

For CI and unit tests, `MockTranscriber` returns a deterministic 5-segment response:

| Segment | Start | End | Text | no_speech_prob | Status |
|---|---|---|---|---|---|
| 1 | 0.0 | 15.0 | "Hello and welcome to this lecture." | 0.05 | Normal |
| 2 | 15.0 | 30.0 | "Today we will cover the main topic." | 0.08 | Normal |
| 3 | 30.0 | 45.0 | "" | 0.85 | **Silent** |
| 4 | 45.0 | 60.0 | "Let us continue with the next point." | 0.10 | Normal |
| 5 | 60.0 | 75.0 | "Thank you for watching." | 0.07 | Normal |

Segment 3 is always silent (no_speech_prob = 0.85 ≥ 0.6, empty text). This makes test assertions deterministic and CI-safe.

---

## Consequences

**Positive:**
- Pipeline logic is decoupled from the transcription library
- Tests run without downloading any model
- Silence detection is deterministic and testable
- Swapping to a different Whisper backend in future requires only a new `Transcriber` implementation

**Negative / Accepted Trade-offs:**
- faster-whisper model download (1–3 GB depending on size) on first run — acceptable with Docker layer caching
- CPU transcription is slow for `large-v3` model (~2× real-time on modern CPU) — acceptable for MVP, GPU support is straightforward to add
- No speaker diarization in MVP (deferred to MVP3)
