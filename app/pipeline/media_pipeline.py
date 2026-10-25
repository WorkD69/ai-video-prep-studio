"""Worker pipeline orchestration — LLM package v2 (ADR 006).

    [url]  download (≤720p) + author's subtitles   stage=downloading
           frame selection (crv, ffmpeg fallback)   stage=frames
           subtitles → VTT segments, else whisper   stage=transcribing
           documents + ZIP                          stage=packaging

SECURITY: job.input_path is resolved and validated under upload_dir before
any media call (URL jobs download to that same trusted UUID-named path).
Only job.id (UUID) is used for the staging dir name. original_filename and
the video title are used only as document text and a sanitised ZIP stem.
"""

import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.config import settings
from app.models.job import Job
from app.pipeline.frames import FrameSelector, select_frames
from app.pipeline.media import MediaError, MediaProber, extract_audio
from app.pipeline.output_assembly import (
    PackageInfo,
    build_failed_parts_md,
    build_metadata_json,
    build_readme_md,
    build_screenshots_manifest_csv,
    build_transcript_json,
    build_transcript_md,
    find_speech_gaps,
)
from app.pipeline.silence import classify_segments
from app.pipeline.subtitles import parse_vtt
from app.pipeline.transcriber import (
    MockTranscriber,
    TranscriptSegment,
    build_hotwords,
    get_transcriber,
)
from app.pipeline.url_source import UrlProbe, UrlSourceError, download_url
from app.pipeline.zip_packaging import build_zip, make_safe_stem

logger = structlog.get_logger()

StageCallback = Callable[[str], None]


def _transcribe(
    job: Job,
    input_path: Path,
    staging_dir: Path,
    title: str,
    language_hint: str | None,
) -> tuple[list[TranscriptSegment], str, str | None]:
    """Whisper (or the CI mock). Returns (segments, transcript_source, language)."""
    transcriber = get_transcriber(
        language=job.language or language_hint,
        hotwords=build_hotwords(settings.whisper_glossary, job.glossary, title),
    )
    audio_path = staging_dir / "audio.wav"
    if isinstance(transcriber, MockTranscriber):
        audio_path.write_bytes(b"")
        source = "mock"
    else:
        extract_audio(str(input_path), audio_path)
        source = f"whisper:{settings.whisper_model}"
    segments = transcriber.transcribe(str(audio_path))
    audio_path.unlink(missing_ok=True)
    language = job.language or getattr(transcriber, "detected_language", None) or language_hint
    return segments, source, language


def run_media_pipeline(
    job: Job,
    prober: MediaProber,
    frame_selector: FrameSelector,
    fallback_selector: FrameSelector | None = None,
    on_stage: StageCallback | None = None,
) -> Path:
    """Orchestrate the package build. Returns absolute path to the produced ZIP."""
    notify = on_stage or (lambda stage: None)

    upload_dir = Path(settings.upload_dir).resolve()
    input_path = Path(job.input_path).resolve()
    if not input_path.is_relative_to(upload_dir):
        raise MediaError("invalid_or_unreadable_media")

    output_dir = Path(settings.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = output_dir / "staging" / str(job.id)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        probe: UrlProbe | None = None
        subtitle_path: Path | None = None
        subtitle_lang: str | None = None

        if job.source_type == "url":
            notify("downloading")
            download = download_url(
                job.source_url,
                input_path,
                staging_dir,
                job.language,
                settings.max_upload_bytes,
            )
            probe = download.probe
            subtitle_path, subtitle_lang = download.subtitle_path, download.subtitle_lang
            span = prober.probe_duration(str(input_path))  # the file we actually got
            if span > settings.max_duration_seconds:
                raise UrlSourceError("duration_limit_exceeded")  # site metadata understated it
        elif job.duration_seconds is not None:
            span = float(job.duration_seconds)
        else:
            span = prober.probe_duration(str(input_path))

        title = probe.title if probe else Path(job.original_filename).stem or "video"

        notify("frames")
        frames_dir = staging_dir / "frames"
        frames, frames_mode = select_frames(
            frame_selector, fallback_selector, str(input_path), frames_dir, span
        )
        frame_seconds = [frame.second for frame in frames]

        notify("transcribing")
        all_segments: list[TranscriptSegment] = []
        transcript_source = ""
        language = None
        if subtitle_path is not None:
            all_segments = parse_vtt(subtitle_path.read_text(encoding="utf-8", errors="replace"))
            transcript_source, language = "manual_subtitles", subtitle_lang
            if not all_segments:
                logger.warning("subtitles_empty_using_whisper", job_id=str(job.id), stage="transcribing")
                subtitle_path = None
        if subtitle_path is None:
            all_segments, transcript_source, language = _transcribe(
                job, input_path, staging_dir, title, probe.language if probe else None
            )

        notify("packaging")
        active_segments, silent_segments = classify_segments(all_segments)
        # make_safe_stem works on a file name; the ".x" keeps dots inside a title
        safe_stem = make_safe_stem(
            f"{title}.x" if probe else job.original_filename, Path(job.stored_filename).stem
        )
        info = PackageInfo(
            job_id=str(job.id),
            title=title,
            original_filename=job.original_filename,
            stored_filename=job.stored_filename,
            source_type=job.source_type or "file",
            source_url=(probe.webpage_url if probe and probe.webpage_url else job.source_url),
            channel=probe.channel if probe else None,
            duration_seconds=span,
            language=language,
            chapters=probe.chapters if probe else [],
            processed_at=datetime.now(tz=timezone.utc),
            transcript_source=transcript_source,
            frames_mode=frames_mode,
            subtitles_filename=subtitle_path.name if subtitle_path else None,
        )
        speech_gaps = find_speech_gaps(active_segments, span)

        zip_path = build_zip(
            output_dir=output_dir,
            safe_stem=safe_stem,
            ts=info.processed_at,
            readme_md=build_readme_md(
                info, len(frames), has_failed_parts=bool(speech_gaps or silent_segments)
            ),
            transcript_md=build_transcript_md(active_segments, frame_seconds, info),
            transcript_json=build_transcript_json(active_segments),
            frames_manifest_csv=build_screenshots_manifest_csv(all_segments, timestamps=frame_seconds),
            failed_parts_md=build_failed_parts_md(silent_segments, speech_gaps),
            metadata_json=build_metadata_json(
                info, all_segments, silent_segments, len(frames), safe_stem
            ),
            frames_dir=frames_dir,
            frame_count=len(frames),
            subtitles_path=subtitle_path,
        )
        logger.info(
            "package_built",
            job_id=str(job.id),
            stage="packaging",
            frames=len(frames),
            frames_mode=frames_mode,
            transcript_source=transcript_source,
            segments=len(active_segments),
        )
        return zip_path

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
