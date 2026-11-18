# AI Handoff - AI Video Prep Studio

## Last Updated
2026-11-07

## Session
Operational recovery and verification only. Existing checkout: C:\Users\Artem\Desktop\ai-video-prep-studio; branch main at f91ea10. No source/config/dependency changes, builds, pulls, migrations, or data cleanup were needed.

Docker Desktop was installed but stopped. Started it without restarting Docker/WSL. Existing app, worker, postgres and redis containers automatically resumed; left running. Host free space: C: about 378 GB, A: about 30 GB.

## Verified
- http://localhost:8000 renders; health reports status/db/redis ok.
- PostgreSQL and Redis healthy; Alembic current is head f1c2d3e4a506.
- RQ worker consumed the test job and completed it in 24.8 seconds.
- NVIDIA RTX 3070 Laptop GPU exposed to worker; CUDA device count 1; existing turbo model cache available.
- Effective backends: ffmpeg, faster_whisper turbo, crv.
- Synthetic 18.4-second speech/video uploaded through browser UI. Job ac4eafc7-216d-48b3-822e-3e0e428a3b9b completed successfully.
- ZIP download returned HTTP 200. CRC, expected v2 files, JSON, 19 JPEG frames, manifest rows and inline references checked. Download and host output SHA256 match.
- Output: A:\aivps-data\outputs\llm_analysis_package_aivps-launch-check_20261107_132001.zip
- Synthetic fixtures/download: C:\Users\Artem\Downloads\aivps-launch-check-20261107; browser-upload fixture also in task workspace.
- Upload is removed after processing. ZIP expires 24 hours after job creation; cleanup interval 600 seconds. Actual passage of 24 hours was not tested.
- URL intake and long/large videos were not tested. No personal videos were used.

## Notes
Startup emits nonblocking RQ/Click duplicate serializer option warnings. No application repair was necessary. Automatic opening of default Windows browser via shell was rejected by tool policy; browser automation successfully opened and exercised localhost.

## Next
User opens http://localhost:8000, selects a video, clicks upload/process, waits for completion and downloads ZIP. Keep Docker Desktop running. Data mounts remain A:/aivps-data and A:/AI/hf-cache. Do not restart Docker/WSL or prune resources used by other projects.
