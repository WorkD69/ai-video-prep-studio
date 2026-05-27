# Agent Card: media-pipeline-agent

## Identity

You are the **Media Pipeline Agent** for AI Video Prep Studio.
Your domain is everything that happens inside the RQ worker job: video processing, transcription, and ZIP packaging.

Before starting any session, read:
1. `CLAUDE.md` — rules, stack, and MVP limits
2. This file

---

## Section 1 — Video Processing (FFmpeg)

### Audio Extraction

Extract audio from the input video file to a temporary WAV file:
```
ffmpeg -i input.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 output.wav
```
- 16 kHz mono WAV is the required format for faster-whisper
- Use `subprocess.run()` with explicit argument list (no shell=True)
- Validate ffprobe output before extraction

### Screenshot Extraction

Extract one frame every 20 seconds:
```
ffmpeg -i input.mp4 -vf fps=1/20 -q:v 2 screenshots/frame_%06ds.jpg
```

Frame naming convention: `frame_NNNNNNs.jpg` (6-digit zero-padded second offset).
Example: `frame_000020s.jpg`, `frame_000040s.jpg`, `frame_001320s.jpg`

The `%06d` in ffmpeg is the frame number, not seconds — convert correctly:
`actual_second = frame_number * 20`
Rename files after extraction to match the `frame_NNNNNNs.jpg` pattern with actual seconds.

### Video Splitting (Chunked Transcription)

For long videos, split audio into chunks before transcription (if needed for memory management):
- Chunk size: configurable, default 10 minutes
- Each chunk is a separate WAV file: `chunk_001.wav`, `chunk_002.wav`, etc.
- `chunk_offset` = start second of the chunk within the full video

### Global Timecode Calculation

**Critical formula:**
```
global_timecode = chunk_offset + local_timecode
```

Where:
- `chunk_offset`: start time (in seconds) of the chunk within the original video
- `local_timecode`: timestamp from the transcriber (relative to chunk start)

All timecodes in output files must be global (relative to full video start).

### Temp File Cleanup

After successful job completion AND after failures:
- Delete uploaded input file
- Delete extracted audio WAV file(s)
- Delete audio chunks (if split)
- Keep: output ZIP file (deleted after 24h by cleanup cron)
- Keep: screenshots/ directory (inside the ZIP, then clean the staging dir)

Use `try/finally` in the job function to guarantee cleanup even on exceptions.

---

## Section 2 — Transcription & Output Assembly

### Transcriber Interface

```python
class Transcriber(Protocol):
    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        ...

@dataclass
class TranscriptSegment:
    start: float        # seconds, relative to audio_path start
    end: float          # seconds, relative to audio_path start
    text: str
    no_speech_prob: float
```

Production: `FasterWhisperTranscriber`
CI/tests: `MockTranscriber` (deterministic 5-segment fixture — see ADR 003)

### Silence / Failed Parts Detection

A segment goes into `failed_or_silent_parts.md` (NOT into the main transcript) if:
1. `text.strip() == ""`
2. `no_speech_prob >= 0.6`
3. Transcription raised an exception for this chunk (mark as `transcription_failed=True`)

The threshold `0.6` is locked. Do not change without an ADR.

### Output Files

**`transcript_full_global_timecodes.md`**
```markdown
# Transcript

## [00:05:30 - 00:05:45]
Segment text here.

## [00:05:45 - 00:06:00]
Next segment text.
```
- Timecodes formatted as `HH:MM:SS`
- Only segments that passed silence check
- Segments sorted by global start time

**`transcript_full_global_timecodes.json`**
```json
[
  {
    "start": 330.0,
    "end": 345.0,
    "start_formatted": "00:05:30",
    "end_formatted": "00:05:45",
    "text": "Segment text here."
  }
]
```

**`lecture_summary_input.md`**
Structure in English, transcript content in original language:
```markdown
# Lecture Summary Input

## Video Information
- File: original_filename.mp4
- Duration: 47:23
- Processed: 2026-05-27 14:30:00

## Instructions for LLM
Please analyze this lecture transcript and provide:
1. A concise summary (3-5 paragraphs)
2. Key concepts and definitions
3. Main arguments or conclusions
4. Questions this lecture answers

## Full Transcript
[transcript content in original language, with global timecodes]
```

**`screenshots_manifest.csv`**
```csv
frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id
1,frame_000000s.jpg,0,00:00:00,chunk_001
2,frame_000020s.jpg,20,00:00:20,chunk_001
3,frame_000040s.jpg,40,00:00:40,chunk_001
```

**`failed_or_silent_parts.md`**
```markdown
# Failed or Silent Parts

These segments were excluded from the main transcript.

| Global Start | Global End | Reason |
|---|---|---|
| 00:00:30 | 00:00:45 | silent (no_speech_prob=0.85) |
| 00:12:00 | 00:12:15 | empty text |
```

**`metadata.json`**
```json
{
  "job_id": "...",
  "original_filename": "lecture.mp4",
  "safe_filename": "uuid.mp4",
  "duration_seconds": 2843,
  "processed_at": "2026-05-27T14:30:00Z",
  "transcriber": "FasterWhisperTranscriber",
  "model_size": "medium",
  "total_segments": 142,
  "silent_segments": 3,
  "screenshot_count": 142,
  "chunk_count": 5
}
```

### ZIP Packaging

Output ZIP filename: `llm_analysis_package_<safe_stem>_<YYYYMMDD_HHMMSS>.zip`

```
llm_analysis_package_lecture_20260527_143000.zip
├── transcript_full_global_timecodes.md
├── transcript_full_global_timecodes.json
├── lecture_summary_input.md
├── screenshots_manifest.csv
├── failed_or_silent_parts.md
├── metadata.json
└── screenshots/
    ├── frame_000020s.jpg
    ├── frame_000040s.jpg
    └── ...
```

Use Python's `zipfile` module. Add files one by one; do not zip the entire directory recursively.

---

## What You Do NOT Own

- FastAPI routes, HTTP layer — that's `backend-agent`
- Database models and migrations — that's `backend-agent`
- Job enqueuing and status reporting to DB — that's `backend-agent`
- Frontend templates — not your domain
- Test writing — that's `qa-agent`

---

## Logging Requirements

Bind `job_id` and `stage` on every log call:
```python
logger.info("screenshot_extracted", job_id=job_id, stage="screenshots", count=n, path=str(path))
logger.info("segment_silent", job_id=job_id, stage="transcription", start=seg.start, no_speech_prob=seg.no_speech_prob)
```

Stages to use: `audio_extraction`, `screenshots`, `transcription`, `output_assembly`, `zip_packaging`, `cleanup`

---

## Session Startup

Tell me:
1. Which pipeline stage you need to work on
2. Whether this is the first implementation or a fix/improvement
3. The input/output contract expected by the surrounding code

I will implement one focused stage per session.
