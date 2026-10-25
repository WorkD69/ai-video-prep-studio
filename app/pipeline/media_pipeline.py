"""Real media pipeline orchestration for Milestone 009.

Screenshots come from an injected ScreenshotExtractor (real ffmpeg or fake);
duration comes from job.duration_seconds (probed at upload) with a defensive
in-worker probe fallback. Transcription stays MockTranscriber until M010.

SECURITY: job.input_path is resolved and validated under upload_dir before
any media call. Only job.id (UUID) is used for the staging dir name.
original_filename is used only for display in documents.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.job import Job
from app.pipeline.media import (
    MediaError,
    MediaProber,
    ScreenshotExtractor,
    screenshot_timestamps,
)
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


def run_media_pipeline(
    job: Job,
    prober: MediaProber,
    extractor: ScreenshotExtractor,
) -> Path:
    """Orchestrate the output pipeline. Returns absolute path to produced ZIP.

    Steps:
    1. Resolve and validate job.input_path under upload_dir; otherwise MediaError.
    2. span = job.duration_seconds (defensive: probe in the worker if None).
    3. timestamps = screenshot_timestamps(span) — single source of truth.
    4. frames = extractor.extract(input, staging/screenshots, timestamps).
    5. Manifest built from the same timestamps list (invariant by construction).
    6. Transcription: MockTranscriber (real transcription is M010).
    7. build_zip with screenshots_dir and screenshot_count=len(frames).
    8. try/finally staging cleanup.
    """
    upload_dir = Path(settings.upload_dir).resolve()
    input_path = Path(job.input_path).resolve()
    if not input_path.is_relative_to(upload_dir):
        raise MediaError("invalid_or_unreadable_media")

    output_dir = Path(settings.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = output_dir / "staging" / str(job.id)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 2: real span — trusted from upload-time probe, else probe now
        if job.duration_seconds is not None:
            span = float(job.duration_seconds)
        else:
            span = prober.probe_duration(str(input_path))

        # Steps 3-4: shared timestamps drive both extractor and manifest
        timestamps = screenshot_timestamps(span)
        screenshots_dir = staging_dir / "screenshots"
        frames = extractor.extract(str(input_path), screenshots_dir, timestamps)

        # Step 6: placeholder audio + MockTranscriber (M009; real is M010)
        audio_path = staging_dir / "audio.wav"
        audio_path.write_bytes(b"")
        transcriber = MockTranscriber()
        all_segments = transcriber.transcribe(str(audio_path))

        active_segments, silent_segments = classify_segments(all_segments)

        processed_at = datetime.now(tz=timezone.utc)

        transcript_md = build_transcript_md(active_segments)
        transcript_json = build_transcript_json(active_segments)
        summary_input_md = build_summary_input_md(
            active_segments,
            original_filename=job.original_filename,
            duration_seconds=span,
            processed_at=processed_at,
        )
        screenshots_manifest_csv_str = build_screenshots_manifest_csv(
            all_segments,
            timestamps=timestamps,
        )
        failed_parts_md = build_failed_parts_md(silent_segments)
        metadata_json = build_metadata_json(
            job_id=str(job.id),
            original_filename=job.original_filename,
            stored_filename=job.stored_filename,
            duration_seconds=span,
            processed_at=processed_at,
            all_segments=all_segments,
            silent_segments=silent_segments,
            screenshot_count=len(frames),
        )

        fallback = Path(job.stored_filename).stem
        safe_stem = make_safe_stem(job.original_filename, fallback)

        # screenshots_dir may not exist when timestamps is empty (span <= 0)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

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
            screenshot_count=len(frames),
            screenshots_dir=screenshots_dir,
        )

        return zip_path

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
