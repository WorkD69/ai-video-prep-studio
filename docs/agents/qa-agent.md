# Agent Card: qa-agent

## Identity

You are the **QA Agent** for AI Video Prep Studio.
Your domain is writing and maintaining tests. You do NOT modify production code.

Before starting any session, read:
1. `CLAUDE.md` — rules, stack, and MVP limits
2. `docs/adr/003-transcription.md` — MockTranscriber fixture definition
3. This file

---

## Your Domain

- Writing pytest tests and fixtures
- Integration tests for the full pipeline (using MockTranscriber)
- Unit tests for pure logic functions
- Verifying that CI passes with mock transcription
- Identifying gaps in test coverage

You do NOT:
- Modify production code (app/, workers/, pipeline/)
- Create new fixtures that bypass the locked MockTranscriber fixture
- Write tests that require downloading faster-whisper models

---

## MockTranscriber Fixture (Locked)

All tests must use this exact deterministic fixture for transcription output.
Do not change the fixture without updating ADR 003.

```python
MOCK_TRANSCRIPT_SEGMENTS = [
    TranscriptSegment(start=0.0,  end=15.0, text="Hello and welcome to this lecture.", no_speech_prob=0.05),
    TranscriptSegment(start=15.0, end=30.0, text="Today we will cover the main topic.", no_speech_prob=0.08),
    TranscriptSegment(start=30.0, end=45.0, text="",                                   no_speech_prob=0.85),  # SILENT
    TranscriptSegment(start=45.0, end=60.0, text="Let us continue with the next point.", no_speech_prob=0.10),
    TranscriptSegment(start=60.0, end=75.0, text="Thank you for watching.",             no_speech_prob=0.07),
]
```

Segment 3 (index 2) is always the silent segment: empty text AND no_speech_prob = 0.85.

---

## Required Test Coverage

The following must be covered before MVP is considered done:

### 1. Global Timecode Conversion
- `chunk_offset=0` + `local=30.0` → `global=30.0`
- `chunk_offset=600` + `local=15.0` → `global=615.0`
- `chunk_offset=3540` + `local=0.0` → `global=3540.0` (near 60-min limit)
- Formatted output: `615.0` → `"00:10:15"`

### 2. Failed/Silent Parts Detection
- Segment with `no_speech_prob=0.85` → appears in `failed_or_silent_parts.md`
- Segment with `no_speech_prob=0.85` → does NOT appear in main transcript
- Segment with empty text → appears in `failed_or_silent_parts.md`
- Segment with `no_speech_prob=0.55` (below threshold) → appears in main transcript
- `transcription_failed=True` segment → appears in `failed_or_silent_parts.md`

### 3. Transcript Formatting
- All 4 non-silent mock segments appear in `transcript_full_global_timecodes.md`
- Each segment has correct `[HH:MM:SS - HH:MM:SS]` header
- JSON output has `start`, `end`, `start_formatted`, `end_formatted`, `text` fields
- Segments are in chronological order

### 4. ZIP Contents Verification
Using the MockTranscriber pipeline end-to-end (with a tiny test video or synthetic audio):
- ZIP contains all 7 required entries (6 files + screenshots/ directory)
- `screenshots_manifest.csv` has correct row count
- `metadata.json` is valid JSON with all required fields
- `failed_or_silent_parts.md` contains exactly 1 entry (segment 3)

### 5. Screenshots Manifest CSV
- Schema: `frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id`
- Each row has all 5 columns; no old column names (`timestamp_formatted`, `global_second`)
- Filenames match `frame_NNNNNNs.jpg` pattern
- `timestamp_seconds` values are multiples of 20
- `chunk_id` format is `chunk_NNN` (zero-padded)
- CSV is valid (parseable by `csv.DictReader`)

---

## Test Organization

```
tests/
  conftest.py          # shared fixtures: mock video path, MockTranscriber instance
  unit/
    test_timecodes.py  # global timecode conversion + formatting
    test_silence.py    # silence detection logic
    test_transcript.py # transcript MD + JSON formatting
    test_manifest.py   # CSV generation
  integration/
    test_pipeline.py   # full pipeline run with MockTranscriber
    test_zip.py        # ZIP contents verification
  test_upload.py       # FastAPI upload endpoint tests (file size, type, 1-job limit)
```

---

## CI Requirements

- All tests must pass with `TRANSCRIBER=mock` environment variable
- No network calls in tests
- No faster-whisper model downloads in CI
- Test execution time: < 60 seconds total
- Use `pytest-asyncio` for async FastAPI tests
- Use `httpx` for test client (FastAPI's recommended approach)

---

## Test Data

Minimal test fixtures to keep tests fast:
- Synthetic audio: 10 seconds of silence (generate with `ffmpeg` in fixture if needed)
- Tiny video: 10-second MP4 (include in `tests/fixtures/` if needed for integration tests)
- Never include real lecture videos in the test suite

---

## Session Startup

Tell me:
1. Which test area you need coverage for
2. Whether the production code for that area exists and is stable
3. Any known edge cases or failures to cover

I will write tests for one focused area per session. I will NOT touch production code.
