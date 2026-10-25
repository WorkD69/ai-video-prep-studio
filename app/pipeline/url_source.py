"""Link intake via the yt-dlp Python API (ADR 006).

SECURITY:
- yt-dlp is driven through its Python API: no subprocess, and the user URL is
  never parsed as command-line options.
- validate_url() is an SSRF guard (any yt-dlp site is allowed): http/https
  only, and the host must resolve to public addresses — no loopback, private,
  link-local or reserved ranges (this also blocks the compose services).
  Residual risk, accepted for a local single-user tool: a public site that
  redirects to an internal address during extraction.
- Errors map to fixed, safe UrlSourceError codes; yt-dlp messages are only
  logged.
"""

import ipaddress
import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger()

_MAX_URL_LENGTH = 2048
_SOCKET_TIMEOUT_SECONDS = 30
_FORMAT = "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"


class UrlSourceError(Exception):
    """Controlled link failure with a safe, user-facing code."""


@dataclass
class UrlProbe:
    title: str
    duration: float
    language: str | None
    manual_subtitle_langs: list[str] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    channel: str | None = None
    webpage_url: str | None = None
    description: str | None = None


@dataclass
class UrlDownload:
    video_path: Path
    subtitle_path: Path | None
    subtitle_lang: str | None
    probe: UrlProbe


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def validate_url(url: str) -> str:
    """Return the stripped URL or raise UrlSourceError("invalid_url" | "forbidden_host")."""
    url = (url or "").strip()
    if not url or len(url) > _MAX_URL_LENGTH or any(ch.isspace() for ch in url):
        raise UrlSourceError("invalid_url")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UrlSourceError("invalid_url")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        raise UrlSourceError("invalid_url")
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_public(a) for a in addresses):
        raise UrlSourceError("forbidden_host")
    return url


def _base_options() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": _SOCKET_TIMEOUT_SECONDS,
        "noprogress": True,
    }


def pick_manual_subtitle_lang(available: list[str], language: str | None) -> str | None:
    """Pick the author's subtitle track in the video's language, if any.

    Exact code first ("ru"), then a regional variant ("ru-RU"). Never a track
    in another language: that would be a translation, not the speech.
    """
    if not language:
        return None
    language = language.lower()
    for code in available:
        if code.lower() == language:
            return code
    for code in available:
        if code.lower().split("-", 1)[0] == language:
            return code
    return None


def _to_probe(info: dict) -> UrlProbe:
    if info.get("_type") in ("playlist", "multi_video") or info.get("entries"):
        raise UrlSourceError("playlist_not_supported")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise UrlSourceError("live_not_supported")
    duration = info.get("duration")
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise UrlSourceError("unknown_duration")
    manual = [code for code in (info.get("subtitles") or {}) if code != "live_chat"]
    chapters = [
        {"start": float(c.get("start_time") or 0), "title": str(c.get("title") or "").strip()}
        for c in (info.get("chapters") or [])
        if str(c.get("title") or "").strip()
    ]
    return UrlProbe(
        title=str(info.get("title") or "video").strip() or "video",
        duration=float(duration),
        language=(info.get("language") or None),
        manual_subtitle_langs=manual,
        chapters=chapters,
        channel=info.get("channel") or info.get("uploader"),
        webpage_url=info.get("webpage_url"),
        description=info.get("description"),
    )


def probe_url(url: str) -> UrlProbe:
    """Read metadata without downloading. Raises UrlSourceError."""
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({**_base_options(), "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        logger.info("url_probe_failed", stage="url_probe", error=str(exc)[:300])
        raise UrlSourceError("unreadable_link")
    if not isinstance(info, dict):
        raise UrlSourceError("unreadable_link")
    return _to_probe(info)


def download_url(
    url: str,
    video_path: Path,
    subtitles_dir: Path,
    language: str | None,
    max_bytes: int,
) -> UrlDownload:
    """Download the video (≤720p, mp4) to video_path and, when the author
    uploaded subtitles in the video's language, that VTT into subtitles_dir.

    video_path is a trusted UUID-named path under upload_dir. Partial files are
    removed on failure.
    """
    import yt_dlp

    stem = video_path.stem
    work_dir = video_path.parent

    def _cleanup_partials() -> None:
        for leftover in work_dir.glob(f"{stem}.*"):
            if leftover != video_path:
                leftover.unlink(missing_ok=True)

    probe = probe_url(url)
    sub_lang = pick_manual_subtitle_lang(probe.manual_subtitle_langs, language or probe.language)
    options = {
        **_base_options(),
        "format": _FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": {"default": str(work_dir / f"{stem}.%(ext)s")},
        "max_filesize": max_bytes,
        "writesubtitles": sub_lang is not None,
        "writeautomaticsub": False,
        "subtitleslangs": [sub_lang] if sub_lang else [],
        "subtitlesformat": "vtt",
        "retries": 3,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        logger.info("url_download_failed", stage="downloading", error=str(exc)[:300])
        _cleanup_partials()
        raise UrlSourceError("download_failed")

    produced = sorted(
        (p for p in work_dir.glob(f"{stem}.*") if p.suffix not in (".vtt", ".part", ".ytdl")),
        key=lambda p: p.suffix != ".mp4",
    )
    if not produced:
        _cleanup_partials()
        raise UrlSourceError("download_failed")
    produced[0].replace(video_path)

    subtitle_path = None
    if sub_lang:
        vtt = work_dir / f"{stem}.{sub_lang}.vtt"
        if vtt.exists():
            subtitles_dir.mkdir(parents=True, exist_ok=True)
            subtitle_path = subtitles_dir / f"subtitles.{sub_lang}.vtt"
            shutil.move(str(vtt), subtitle_path)  # upload_dir and output_dir may be different mounts
    _cleanup_partials()
    if subtitle_path is None:
        sub_lang = None
    return UrlDownload(video_path=video_path, subtitle_path=subtitle_path, subtitle_lang=sub_lang, probe=probe)
