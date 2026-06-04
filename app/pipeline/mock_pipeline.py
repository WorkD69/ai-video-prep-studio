"""Mock pipeline orchestration for Milestone 004.

Runs the full output pipeline using MockTranscriber.
No real ffmpeg, no faster-whisper, no subprocess calls.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.job import Job
from app.pipeline.output_assembly import (
    build_failed_parts_md,
    build_metadata_json,
    build_screenshots_manifest_csv,
    build_summary_input_md,
    build_transcript_json,
    build_transcript_md,
)
from app.pipeline.silence import classify_segments
from app.pipeline.transcriber import MockTranscriber
from app.pipeline.zip_packaging import make_safe_stem, build_zip


def run_mock_output_pipeline(job: Job) -> Path:
    """Orchestrate mock output pipeline. Returns absolute path to produced ZIP.

    Steps:
    1. Create per-job staging dir: output_dir/staging/{job.id}/
    2. Create a placeholder audio file in staging: audio.wav (empty content)
    3. Transcribe with MockTranscriber
    4. Classify segments
    5. Build all output documents
    6. Build ZIP in output_dir (NOT in staging)
    7. Cleanup staging dir in try/finally

    SECURITY: job.input_path and similar user-controlled values are never used
    in filesystem paths. Only job.id (UUID) is used for the staging dir name.
    original_filename is used only for display (readability) in documents.
    """
    output_dir = Path(settings.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = output_dir / "staging" / str(job.id)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 2: placeholder audio file (MockTranscriber doesn't read it)
        audio_path = staging_dir / "audio.wav"
        audio_path.write_bytes(b"")

        # Step 3: transcribe
        transcriber = MockTranscriber()
        all_segments = transcriber.transcribe(str(audio_path))

        # Step 4: classify
        active_segments, silent_segments = classify_segments(all_segments)

        # Step 5: build documents
        processed_at = datetime.now(tz=timezone.utc)

        transcript_md = build_transcript_md(active_segments)
        transcript_json = build_transcript_json(active_segments)
        summary_input_md = build_summary_input_md(
            active_segments,
            original_filename=job.original_filename,
            duration_seconds=job.duration_seconds,
            processed_at=processed_at,
        )
        screenshots_manifest_csv_str = build_screenshots_manifest_csv(
            all_segments,
            duration_seconds=job.duration_seconds,
        )
        # Derive screenshot_count from manifest rows directly — guarantees invariant:
        # metadata.screenshot_count == manifest rows == files in screenshots/
        manifest_rows = screenshots_manifest_csv_str.strip().splitlines()
        screenshot_count = len(manifest_rows) - 1  # minus header row

        failed_parts_md = build_failed_parts_md(silent_segments)
        metadata_json = build_metadata_json(
            job_id=str(job.id),
            original_filename=job.original_filename,
            stored_filename=job.stored_filename,
            duration_seconds=job.duration_seconds,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=screenshot_count,
        )

        # safe_stem: use original_filename for display readability;
        # fallback to UUID stem of stored_filename (already safe)
        fallback = Path(job.stored_filename).stem
        safe_stem = make_safe_stem(job.original_filename, fallback)

        zip_path = build_zip(
            output_dir=output_dir,
            safe_stem=safe_stem,
            ts=processed_at,
            transcript_md=transcript_md,
            transcript_json=transcript_json,
            summary_input_md=summary_input_md,
            screenshots_manifest_csv=screenshots_manifest_csv_str,
            failed_parts_md=failed_parts_md,
            metadata_json=metadata_json,
            screenshot_count=screenshot_count,
        )

        return zip_path

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)