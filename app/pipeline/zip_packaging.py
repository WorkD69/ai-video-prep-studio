"""ZIP packaging for the LLM package v2 (ADR 006).

Handles safe filename sanitization and ZIP assembly.
All ZIP entry paths are fixed strings or validated patterns — never derived
from user input.
"""

import re
import zipfile
from datetime import datetime
from pathlib import Path

_ALLOWED_PATTERN = re.compile(r"[^a-zA-Z0-9_\-]")
_UNDERSCORE_RUNS = re.compile(r"_{2,}")
_FRAME_NAME = re.compile(r"^frame_\d{6}s\.jpg$")
_SUBTITLES_NAME = re.compile(r"^subtitles\.[A-Za-z0-9-]{1,20}\.vtt$")
# Cyrillic titles are common (ADR 006): transliterate so the ZIP name stays readable.
_CYRILLIC = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o",
     "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e",
     "yu", "ya", "i", "yi", "ye", "g"],
))


def _transliterate(text: str) -> str:
    out = []
    for ch in text:
        latin = _CYRILLIC.get(ch.lower())
        if latin is None:
            out.append(ch)
        else:
            out.append(latin.capitalize() if ch.isupper() else latin)
    return "".join(out)


def make_safe_stem(original_filename: str, fallback: str) -> str:
    """Sanitize original_filename for use in a ZIP filename.

    Rules (from spec):
    - Start from Path(original_filename).stem
    - Transliterate Cyrillic to Latin (ADR 006)
    - Allow only [a-zA-Z0-9_-]
    - Replace every other character with _, collapse runs of _
    - Trim leading/trailing _
    - Cap at 64 characters
    - If result is empty, return fallback
    """
    stem = _transliterate(Path(original_filename).stem)
    sanitized = _UNDERSCORE_RUNS.sub("_", _ALLOWED_PATTERN.sub("_", stem))
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
    *,
    readme_md: str,
    transcript_md: str,
    transcript_json: str,
    frames_manifest_csv: str,
    failed_parts_md: str,
    metadata_json: str,
    frames_dir: Path,
    frame_count: int,
    subtitles_path: Path | None = None,
) -> Path:
    """Create ZIP at output_dir/filename. Returns absolute path.

    ZIP contains:
    - README.md, transcript.md, transcript.json, frames_manifest.csv,
      failed_parts.md, metadata.json
    - frames/ with every frame_NNNNNNs.jpg from frames_dir (sorted), guarded by
      the invariant len(files) == frame_count
    - subtitles.<lang>.vtt when subtitles_path is given

    JPEGs are stored without recompression (already compressed).
    output_dir must exist before calling this function.
    """
    if not output_dir.exists():
        raise ValueError(f"output_dir does not exist: {output_dir}")

    frame_files = sorted(p for p in frames_dir.glob("frame_*.jpg") if _FRAME_NAME.match(p.name))
    if len(frame_files) != frame_count:
        raise ValueError(
            f"frame invariant violated: {len(frame_files)} files in frames_dir, "
            f"expected {frame_count}"
        )
    if subtitles_path is not None and not _SUBTITLES_NAME.match(subtitles_path.name):
        raise ValueError(f"unexpected subtitles file name: {subtitles_path.name}")

    zip_path = output_dir / make_zip_filename(safe_stem, ts)

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("README.md", readme_md.encode("utf-8"))
            zf.writestr("transcript.md", transcript_md.encode("utf-8"))
            zf.writestr("transcript.json", transcript_json.encode("utf-8"))
            zf.writestr("frames_manifest.csv", frames_manifest_csv.encode("utf-8"))
            zf.writestr("failed_parts.md", failed_parts_md.encode("utf-8"))
            zf.writestr("metadata.json", metadata_json.encode("utf-8"))
            zf.writestr("frames/", b"")
            for frame_file in frame_files:
                zf.write(frame_file, f"frames/{frame_file.name}", compress_type=zipfile.ZIP_STORED)
            if subtitles_path is not None:
                zf.write(subtitles_path, subtitles_path.name)
    except Exception:
        if zip_path.exists():
            zip_path.unlink()
        raise

    return zip_path.resolve()
