from pathlib import Path

from fastapi import HTTPException, UploadFile


ALLOWED_MIME_TYPES = {"video/mp4", "video/webm", "video/x-matroska", "video/quicktime"}
ALLOWED_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov"}

_MP4_MOV_MIME = {"video/mp4", "video/quicktime"}
_WEBM_MKV_MIME = {"video/webm", "video/x-matroska"}


def validate_content_type(content_type: str) -> None:
    mime = content_type.split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported media type: {mime}")


def validate_extension(filename: str) -> str:
    """Returns safe extension (e.g. '.mp4') or raises 415."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported file extension: {ext}")
    return ext


def validate_magic_bytes(header: bytes, content_type: str) -> None:
    mime = content_type.split(";")[0].strip().lower()
    if mime in _MP4_MOV_MIME:
        if len(header) < 8 or header[4:8] != b"ftyp":
            raise HTTPException(status_code=400, detail="Magic byte mismatch: not a valid MP4/MOV file")
    elif mime in _WEBM_MKV_MIME:
        if len(header) < 4 or header[0:4] != b"\x1a\x45\xdf\xa3":
            raise HTTPException(status_code=400, detail="Magic byte mismatch: not a valid WebM/MKV file")
    else:
        raise HTTPException(status_code=415, detail=f"Unsupported media type: {mime}")


async def stream_save(file: UploadFile, dest_path: Path, first_bytes: bytes, max_bytes: int) -> int:
    """Stream file to dest_path. Returns total bytes written. Cleans up partial file on any failure."""
    if len(first_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail="File too large. Maximum upload size is 500 MB.",
        )
    total = len(first_bytes)
    try:
        with dest_path.open("wb") as f:
            f.write(first_bytes)
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="File too large. Maximum upload size is 500 MB.",
                    )
                f.write(chunk)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise
    return total
