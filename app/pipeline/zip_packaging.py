"""ZIP packaging utilities for Milestone 004.

Handles safe filename sanitization and ZIP assembly.
All ZIP entry paths are fixed strings — never derived from user input.
"""

import re
import zipfile
from datetime import datetime
from pathlib import Path

_SCREENSHOT_INTERVAL = 20  # seconds, locked

# Minimal valid 1x1 white JPEG (no PIL dependency)
_PLACEHOLDER_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
)

_ALLOWED_PATTERN = re.compile(r"[^a-zA-Z0-9_\-]")


def make_safe_stem(original_filename: str, fallback: str) -> str:
    """Sanitize original_filename for use in a ZIP filename.

    Rules (from spec):
    - Start from Path(original_filename).stem
    - Allow only [a-zA-Z0-9_-]
    - Replace every other character with _
    - Trim leading/trailing _
    - Cap at 64 characters
    - If result is empty, return fallback
    """
    stem = Path(original_filename).stem
    sanitized = _ALLOWED_PATTERN.sub("_", stem)
    sanitized = sanitized[:64]            # cap first
    sanitized = sanitized.strip("_")     # strip AFTER cap
    if not sanitized:
        return fallback
    return sanitized


def make_zip_filename(safe_stem: str, ts: datetime) -> str:
    """Returns: llm_analysis_package_{safe_stem}_{YYYYMMDD_HHMMSS}.zip"""
    ts_str = ts.strftime("%Y%m%d_%H%M%S")
    return f"llm_analysis_package_{safe_stem}_{ts_str}.zip"


def build_zip(
    output_dir: Path,
    safe_stem: str,
    ts: datetime,
    transcript_md: str,
    transcript_json: str,
    summary_input_md: str,
    screenshots_manifest_csv: str,
    failed_parts_md: str,
    metadata_json: str,
    screenshot_count: int,
) -> Path:
    """Create ZIP at output_dir/filename. Returns absolute path.

    ZIP contains exactly:
    - transcript_full_global_timecodes.md
    - transcript_full_global_timecodes.json
    - lecture_summary_input.md
    - screenshots_manifest.csv
    - failed_or_silent_parts.md
    - metadata.json
    - screenshots/ directory with screenshot_count placeholder JPG files

    SECURITY: ZIP entry paths are fixed strings only — never derived from user input.
    Screenshot filenames derived from index only (frame_{ts:06d}s.jpg).
    output_dir must exist before calling this function.
    """
    if not output_dir.exists():
        raise ValueError(f"output_dir does not exist: {output_dir}")

    zip_filename = make_zip_filename(safe_stem, ts)
    zip_path = output_dir / zip_filename

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("transcript_full_global_timecodes.md", transcript_md.encode("utf-8"))
            zf.writestr("transcript_full_global_timecodes.json", transcript_json.encode("utf-8"))
            zf.writestr("lecture_summary_input.md", summary_input_md.encode("utf-8"))
            zf.writestr("screenshots_manifest.csv", screenshots_manifest_csv.encode("utf-8"))
            zf.writestr("failed_or_silent_parts.md", failed_parts_md.encode("utf-8"))
            zf.writestr("metadata.json", metadata_json.encode("utf-8"))
            zf.writestr("screenshots/", b"")

            for i in range(screenshot_count):
                timestamp = i * _SCREENSHOT_INTERVAL
                filename = f"frame_{timestamp:06d}s.jpg"
                zf.writestr(f"screenshots/{filename}", _PLACEHOLDER_JPEG)
    except Exception:
        if zip_path.exists():
            zip_path.unlink()
        raise

    return zip_path.resolve()
