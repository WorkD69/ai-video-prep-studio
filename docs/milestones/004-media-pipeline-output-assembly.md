# Milestone 004 - Media Pipeline Output Assembly with MockTranscriber

## Summary

Add the first real media-pipeline contract behind the existing RQ worker, still using
deterministic test doubles for heavy media work. After M004, a worker job produces a real
ZIP artifact and updates `jobs.output_path`:

```
queued -> processing -> done + output_path
                  \-> failed + error_message
```

M004 is the bridge between the M003 mock lifecycle and the full MVP pipeline. It proves the
output file contract, ZIP layout, transcript formatting, silence handling, and download-ready
artifact path without requiring faster-whisper model downloads in CI.

## Branch naming

- Spec (docs-only): `docs/milestone-004-media-pipeline-output-spec`
- Future implementation: `feature/milestone-004-media-pipeline-output-assembly`

## Reference repo note

The repo at `C:\Users\Artem\Desktop\lection\hseproject_videoanalyser` may be used as a
reference for ideas only. Even with permission available, do not copy code blindly: its
architecture differs from this project (SQLite-oriented models, different statuses, different
result contract, no required LLM ZIP package). Any borrowed implementation detail must be
re-designed for this codebase and documented in review if copied directly.

Useful reference ideas:
- Use `subprocess.run([...], shell=False)` for future ffmpeg calls.
- Keep a per-job staging directory.
- Test heavy pipeline paths with fake media/transcription functions.
- Compact low-level media errors before storing safe user-visible messages.

## In Scope

- Introduce pipeline domain modules under `app/pipeline/` for:
  - transcript segment data structures and `Transcriber` protocol;
  - deterministic `MockTranscriber`;
  - global timecode formatting and conversion;
  - silence/failed segment classification;
  - output document assembly;
  - ZIP packaging.
- Replace M003's sleep-only worker success path with a mock output pipeline.
- Create a per-job staging directory under an `output_dir` setting.
- Generate a real ZIP file at:
  `outputs/llm_analysis_package_<safe_stem>_<YYYYMMDD_HHMMSS>.zip`.
- Set `jobs.output_path` on success.
- Preserve guarded worker transitions from M003.
- Keep tests deterministic, fast, and independent of faster-whisper downloads.
- Add focused unit/integration tests for timecodes, silence handling, output files, ZIP contents,
  and worker success/failure behavior.
- Record gates and decisions in `AI_WORKLOG.md` during implementation.

## Out of Scope

- Real `ffmpeg` / `ffprobe` execution.
- Real screenshot extraction from uploaded video.
- Real audio extraction.
- Real faster-whisper transcription.
- Duration limit enforcement via ffprobe.
- `GET /download/{job_id}` endpoint.
- Status page / polling UI.
- 1 active job per session/IP.
- 24h cleanup/reaper.
- Retry logic.
- New database migration.
- Skills / MCP.

## Current state after M003

- `POST /jobs/upload` saves an input file, creates a job row, enqueues RQ, and returns `queued`.
- `process_job(job_id)` opens its own DB session and performs guarded transitions.
- Successful M003 mock processing sets `status=done` and `completed_at`, but leaves
  `output_path=NULL`.
- `outputs/` is already mounted into both `app` and `worker` containers.
- The `jobs` table already has nullable `output_path` and `duration_seconds`; no schema change is
  needed for M004.

## Pipeline contract

Add a pure orchestration function, callable from the worker:

```python
def run_mock_output_pipeline(job: Job) -> Path:
    ...
```

Requirements:
- Input is the DB `Job` object after the worker has moved it to `processing`.
- Read only trusted DB fields: `id`, `original_filename`, `stored_filename`, `input_path`,
  `video_size_bytes`, `duration_seconds`.
- Do not trust or execute user-provided paths. `input_path` must be normalized and checked before
  any file operation if it is used.
- Return the absolute path to the produced ZIP.
- Raise a controlled exception on failure; worker stores `error_message="processing_failed"`.
- Clean the per-job staging directory with `try/finally` on both success and failure. This is
  separate from the deferred 24h output ZIP retention/reaper.

M004 may use a synthetic/mock media source for tests, but the implementation must not fake the ZIP
after the worker boundary. The worker must call the pipeline and persist the returned `output_path`.

## Transcriber contract

Use ADR 003 exactly:

```python
class Transcriber(Protocol):
    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        ...

@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    no_speech_prob: float
```

`MockTranscriber` returns the locked 5-segment fixture from ADR 003. Segment 3 is silent:

```text
start=30.0, end=45.0, text="", no_speech_prob=0.85
```

In M004 the `audio_path` argument may point to a deterministic placeholder file created in the
staging directory. The interface must still be real so faster-whisper can replace it in a later
milestone.

## Timecode contract

Add pure helpers:

```python
def to_global_time(local_seconds: float, chunk_offset: float) -> float:
    return chunk_offset + local_seconds

def format_hhmmss(seconds: float) -> str:
    ...
```

Required examples:
- `chunk_offset=0`, `local=30.0` -> `30.0`
- `chunk_offset=600`, `local=15.0` -> `615.0`
- `chunk_offset=3540`, `local=0.0` -> `3540.0`
- `615.0` -> `"00:10:15"`

## Silence and failed parts contract

A segment is excluded from the main transcript and included in `failed_or_silent_parts.md` if any
condition is true:

- `text.strip() == ""`
- `no_speech_prob >= 0.6`
- a chunk-level transcription failure is represented as `transcription_failed=True`

The `0.6` threshold is locked by CLAUDE.md / ADR 003 and must not change without ADR.

## ZIP contents

The ZIP must contain exactly these top-level files plus a `screenshots/` directory:

```text
transcript_full_global_timecodes.md
transcript_full_global_timecodes.json
lecture_summary_input.md
screenshots_manifest.csv
failed_or_silent_parts.md
metadata.json
screenshots/
```

For M004 screenshots are deterministic placeholders, not frames extracted by ffmpeg. The filenames
and manifest still obey the production contract so later ffmpeg implementation can replace only the
producer, not the ZIP/output contract.

### ZIP safe stem

`safe_stem` is derived from `original_filename` for readability, but it must be sanitized before it
is used in a filesystem path or ZIP filename:

- start from `Path(original_filename).stem`;
- allow only `[a-zA-Z0-9_-]`;
- replace every other character with `_`;
- reject path separators, dots, `.` and `..` as meaningful path components;
- trim leading/trailing `_`;
- cap length at 64 characters;
- if the result is empty, fall back to the UUID stem from `stored_filename`.

The final ZIP filename must stay under `output_dir` and must not contain raw user-controlled path
segments from `original_filename`.

## Output file contracts

### transcript_full_global_timecodes.md

```markdown
# Transcript

## [00:00:00 - 00:00:15]
Hello and welcome to this lecture.
```

- Include only non-silent segments.
- Use global timecodes.
- Sort by global start time.

### transcript_full_global_timecodes.json

```json
[
  {
    "start": 0.0,
    "end": 15.0,
    "start_formatted": "00:00:00",
    "end_formatted": "00:00:15",
    "text": "Hello and welcome to this lecture."
  }
]
```

### lecture_summary_input.md

Use English section headers and preserve transcript text in its original language:

```markdown
# Lecture Summary Input

## Video Information
- File: lecture.mp4
- Duration: unknown
- Processed: 2026-06-03T12:00:00Z

## Instructions for LLM
Please analyze this lecture transcript and provide:
1. A concise summary (3-5 paragraphs)
2. Key concepts and definitions
3. Main arguments or conclusions
4. Questions this lecture answers

## Full Transcript
...
```

### screenshots_manifest.csv

Schema is locked:

```csv
frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id
1,frame_000000s.jpg,0,00:00:00,chunk_001
```

M004 should create deterministic placeholder screenshot entries. If the mock duration is unknown,
use the transcript span to produce timestamps at 20-second intervals starting at 0. For the default
MockTranscriber fixture (span `0-75s`), M004 creates exactly four placeholder screenshots:
`0`, `20`, `40`, and `60` seconds.

Required invariant:

```text
metadata.screenshot_count == rows in screenshots_manifest.csv == files in screenshots/
```

### failed_or_silent_parts.md

```markdown
# Failed or Silent Parts

These segments were excluded from the main transcript.

| Global Start | Global End | Reason |
|---|---|---|
| 00:00:30 | 00:00:45 | silent (no_speech_prob=0.85); empty text |
```

If multiple exclusion conditions apply to the same segment, combine them in one deterministic
reason string. For the default MockTranscriber fixture, the reason string is exactly:
`silent (no_speech_prob=0.85); empty text`.

### metadata.json

Required fields:

```json
{
  "job_id": "...",
  "original_filename": "lecture.mp4",
  "safe_filename": "uuid.mp4",
  "duration_seconds": null,
  "processed_at": "2026-06-03T12:00:00Z",
  "transcriber": "MockTranscriber",
  "model_size": "mock",
  "total_segments": 5,
  "silent_segments": 1,
  "screenshot_count": 4,
  "chunk_count": 1
}
```

## Worker behavior changes

Keep M003 guarded transitions:

1. `pending|queued -> processing`
2. call `run_mock_output_pipeline(job)`
3. success writes `status=done`, `completed_at`, and `output_path`
   with `WHERE status='processing'`
4. failure writes `status=failed`, `error_message='processing_failed'`, and `completed_at`
   with `WHERE status='processing'`, then re-raises for RQ failed registry

Do not set `done` if ZIP creation failed. Do not overwrite a terminal state if a race changed the
job away from `processing`.

## Files expected in implementation

Suggested shape:

```text
app/pipeline/transcriber.py
app/pipeline/timecodes.py
app/pipeline/silence.py
app/pipeline/output_assembly.py
app/pipeline/zip_packaging.py
app/pipeline/mock_pipeline.py
tests/unit/test_timecodes.py
tests/unit/test_silence.py
tests/unit/test_output_assembly.py
tests/integration/test_zip.py
tests/test_worker.py
```

Exact filenames may change if the implementation finds a cleaner local pattern, but the contracts
above must remain stable.

## Settings

Add only if needed:

| Setting | Default | Purpose |
|---|---|---|
| `output_dir` | `./outputs` | Final ZIP location shared by app and worker. |
| `pipeline_staging_dir` | `./outputs/staging` | Temporary per-job output assembly directory. |
| `transcriber` | `mock` in tests, production value later | Selects transcriber implementation. |

No secrets. No external API keys.

## Testing requirements

Tests must be deterministic and should not require network, faster-whisper, or real ffmpeg.

Required coverage:
- global timecode conversion and HH:MM:SS formatting;
- silence detection for empty text, `no_speech_prob >= 0.6`, below-threshold speech, and
  transcription failure;
- transcript Markdown and JSON generation;
- manifest CSV schema and filename pattern `frame_NNNNNNs.jpg`;
- screenshot determinism: `metadata.screenshot_count`, manifest rows, and files in `screenshots/`
  are equal;
- ZIP contains all required files and `screenshots/` entries;
- ZIP safe stem sanitization for `original_filename` containing `../`, dots, unicode, spaces, and
  overlong strings;
- worker success sets `done`, `completed_at`, and non-null `output_path`;
- worker failure during pipeline assembly sets `failed`, safe `error_message`, and re-raises;
- staging cleanup runs on failure and does not accumulate per-job staging files;
- existing M003 enqueue/lifecycle tests remain green.

Target: `pytest tests/ -v` under 60 seconds.

## Security gate

- No `shell=True`.
- Any future subprocess calls must use explicit argument lists.
- Do not pass user-controlled strings into shell commands.
- Normalize paths and ensure generated files stay under configured `output_dir` / staging dirs.
- ZIP entries must use fixed relative names; never derive archive paths from user input.
- ZIP filenames must use only sanitized `safe_stem`, never raw `original_filename`.
- Do not include raw tracebacks, absolute host paths, secrets, or environment values in
  user-visible output files or `error_message`.
- Do not accept ZIP input in this milestone, so zip-bomb input risk is N/A.

## Docker / manual gates

Canonical gate:

```powershell
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app worker
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json
```

Manual smoke for implementation:
- upload a tiny accepted MP4 test file;
- poll `GET /jobs/{job_id}` until `done`;
- verify `output_path` is non-null in job response or DB inspection;
- inspect the ZIP and confirm all required entries exist.

## Acceptance criteria

- AC1 - M004 introduces a real ZIP-producing mock output pipeline behind the worker.
- AC2 - Successful worker processing writes `status=done`, `completed_at`, and non-null
  `output_path`.
- AC3 - The ZIP filename follows
  `llm_analysis_package_<safe_stem>_<YYYYMMDD_HHMMSS>.zip`.
- AC4 - `safe_stem` is sanitized from `original_filename`; filenames containing `../`, dots,
  unicode, spaces, or overlong strings cannot escape `output_dir` or produce unsafe ZIP names.
- AC5 - Main transcript excludes the locked silent MockTranscriber segment.
- AC6 - `failed_or_silent_parts.md` includes exactly the locked silent segment for the default
  MockTranscriber fixture with reason `silent (no_speech_prob=0.85); empty text`.
- AC7 - `screenshots_manifest.csv` uses exactly
  `frame_index,filename,timestamp_seconds,timestamp_hhmmss,chunk_id`.
- AC8 - The ZIP contains all required files and a `screenshots/` directory.
- AC9 - `metadata.json` is valid JSON and includes all required fields.
- AC10 - `metadata.screenshot_count` equals manifest row count and the number of files in
  `screenshots/`; for the default fixture the count is 4.
- AC11 - Worker failure during output assembly writes `failed`, safe `error_message`, and
  re-raises.
- AC12 - Staging cleanup runs on both success and failure; per-job staging directories do not
  accumulate after pipeline execution.
- AC13 - `output_path` is written only together with `done` under `WHERE status='processing'`; no
  partial ZIP is treated as a successful artifact.
- AC14 - No real faster-whisper model download is required in tests or CI.
- AC15 - No real ffmpeg/ffprobe subprocess is required in M004.
- AC16 - No DB migration is introduced.
- AC17 - Existing M001-M003 tests remain green.
- AC18 - Docker canonical gate passes and is recorded in `AI_WORKLOG.md`.

## Implementation order

1. Add timecode and silence pure helpers with unit tests.
2. Add `Transcriber`, `TranscriptSegment`, and `MockTranscriber` from ADR 003.
3. Add output assembly functions for MD, JSON, CSV, metadata, and failed/silent report.
4. Add ZIP packaging with fixed archive names.
5. Add mock pipeline orchestration and staging cleanup.
6. Wire worker success path to call the mock pipeline and persist `output_path`.
7. Add worker failure tests for pipeline exceptions.
8. Run `pytest tests/ -v`.
9. Run Docker canonical gate and manual ZIP smoke.
10. Update `AI_WORKLOG.md`.
11. Run Security Agent and Codex Reviewer in clean contexts.

## Reliability gate

- Guarded worker transitions from M003 remain in place.
- `output_path` is persisted only with `done` under `WHERE status='processing'`.
- A failed ZIP assembly does not leave a job in `done` and does not expose a partial ZIP as an
  artifact.
- Per-job staging cleanup runs on both success and failure.
- 24h retention/reaper remains out of scope.

## Documentation gate

`docs/agents/media-pipeline-agent.md` is the source of truth for output contracts. If this spec
changes output filenames, ZIP layout, manifest schema, or failed/silent reason formatting, update
that agent card in the same docs PR.

Timestamp fields such as `processed_at` and the `<YYYYMMDD_HHMMSS>` ZIP suffix should be tested by
format/regex, not by exact wall-clock values.

## Open questions / deferred risks

- Real duration detection remains deferred until ffprobe milestone.
- Real screenshots remain deferred until ffmpeg milestone.
- `GET /download/{job_id}` remains deferred; M004 only makes the artifact path real.
- Per-job staging cleanup must be tested, but 24h retention/reaper remains deferred.
- If implementation discovers that mock ZIP + real download should be split differently, stop and
  check with Process Mentor before broadening scope.
