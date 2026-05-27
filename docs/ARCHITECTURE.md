# System Architecture

## Overview

AI Video Prep Studio processes uploaded videos asynchronously and produces a structured ZIP package
ready for LLM analysis (ChatGPT, Claude, etc.).

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              USER BROWSER                               │
│                                                                         │
│   Upload form (HTMX)          Status polling (HTMX SSE / polling)      │
│   ─────────────────           ────────────────────────────────────      │
│         │                                    ▲                          │
└─────────┼────────────────────────────────────┼──────────────────────────┘
          │ HTTP POST /upload                  │ HTTP GET /status/{job_id}
          ▼                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│                           FASTAPI APP                                   │
│                                                                         │
│   POST /upload     GET /status/{id}    GET /download/{id}               │
│   ─────────────    ────────────────    ──────────────────               │
│         │                │                      │                       │
│         ▼                ▼                      ▼                       │
│   Validate file     Read job          Stream ZIP file                   │
│   Save to disk      from DB                                             │
│   Enqueue RQ job ───────────────────────────────────────────────────►  │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
             ┌───────────────▼───────────────┐
             │         REDIS                 │
             │   RQ job queue                │
             │   Job status cache            │
             └───────────────┬───────────────┘
                             │ Worker picks up job
                             ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         RQ WORKER PROCESS                              │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                    MEDIA PIPELINE                               │  │
│  │                                                                 │  │
│  │  1. Extract audio (ffmpeg)                                      │  │
│  │  2. Extract screenshots every 20s (ffmpeg)                      │  │
│  │     → frame_NNNNNNs.jpg                                         │  │
│  │                                                                 │  │
│  │  3. Split video into chunks if > threshold                      │  │
│  │     → global_timecode = chunk_offset + local_timecode           │  │
│  │                                                                 │  │
│  │  4. Transcribe via Transcriber interface                        │  │
│  │     → FasterWhisperTranscriber (prod)                           │  │
│  │     → MockTranscriber (CI/tests)                                │  │
│  │                                                                 │  │
│  │  5. Build output files:                                         │  │
│  │     ├── transcript_full_global_timecodes.md                     │  │
│  │     ├── transcript_full_global_timecodes.json                   │  │
│  │     ├── lecture_summary_input.md                                │  │
│  │     ├── screenshots_manifest.csv                                │  │
│  │     ├── failed_or_silent_parts.md                               │  │
│  │     ├── metadata.json                                           │  │
│  │     └── screenshots/frame_*.jpg                                 │  │
│  │                                                                 │  │
│  │  6. Package into ZIP                                            │  │
│  │     → llm_analysis_package_<stem>_<timestamp>.zip              │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  7. Update job status in PostgreSQL                                    │
│  8. Clean up temp files                                                │
└─────────────────────────┬──────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────┐
│           POSTGRESQL                    │
│                                         │
│  jobs table                             │
│    id, status, created_at,              │
│    original_filename, output_path,      │
│    error_message, session_id            │
│                                         │
│  usage_log table                        │
│    id, job_id, event, timestamp         │
└─────────────────────────────────────────┘
```

## Data Flow Summary

```
Video file (upload)
  → temp storage (uploads/)
  → audio extraction
  → screenshot extraction (every 20s)
  → transcription (faster-whisper)
  → output file assembly
  → ZIP packaging (outputs/)
  → temp cleanup
  → ZIP available for download (24h retention)
```

## Deployment Topology

```
┌──────────────────────────────────────────────────┐
│                 docker-compose                    │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  app     │  │  worker  │  │  redis        │  │
│  │ FastAPI  │  │  RQ      │  │               │  │
│  │ :8000    │  │          │  │  :6379        │  │
│  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
│       │             │                │           │
│       └─────────────┴────────────────┘           │
│                      │                           │
│              ┌───────▼───────┐                   │
│              │  postgres     │                   │
│              │  :5432        │                   │
│              └───────────────┘                   │
└──────────────────────────────────────────────────┘
```

## Key Design Decisions

- **Single worker process** in MVP — no horizontal scaling needed for 1 job/session limit
- **Shared volume** between `app` and `worker` containers for uploads/ and outputs/
- **No S3 in MVP** — local filesystem storage with 24h cleanup cron
- **Transcriber interface** isolates faster-whisper dependency from pipeline logic
- **structlog** ensures every log line carries `job_id` and `stage` for traceability
