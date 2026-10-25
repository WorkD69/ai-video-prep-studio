"""Link intake via the yt-dlp Python API (ADR 006).

SECURITY:
- yt-dlp is driven through its Python API: no subprocess, and the user URL is
  never parsed as command-line options.
- SSRF (any yt-dlp site is allowed): validate_url() rejects non-http(s) URLs,
  embedded credentials and hosts resolving to non-public addresses. That
  check alone runs once, before yt-dlp, so every yt-dlp call additionally runs
  under public_only_dns(): each DNS lookup made by the calling thread must
  resolve to public addresses at connect time. This covers DNS rebinding,
  redirects and URLs nested in pages/manifests (HLS stays on yt-dlp's native
  downloader, which resolves through Python). Residual risk, accepted for a
  local single-user tool: lookups made off-thread (e.g. websockets via an
  asyncio executor) are not guarded.
- Sizes are enforced on the bytes actually written (progress hook + final
  check), not only on Content-Length; partial files are removed on any error.
- Errors map to fixed, safe UrlSourceError codes; yt-dlp messages are only
  logged.
"""

import ipaddress
import re
import shutil
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger()

_MAX_URL_LENGTH = 2048
_SOCKET_TIMEOUT_SECONDS = 30
_FORMAT = "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"
_SUBTITLE_LANG = re.compile(r"^[A-Za-z0-9-]{1,20}$")


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


# ---------------------------------------------------------------------------
# connect-time DNS guard
# ---------------------------------------------------------------------------

_dns_guard = threading.local()


def _guarded(original):
    def getaddrinfo(host, *args, **kwargs):
        infos = original(host, *args, **kwargs)
        if getattr(_dns_guard, "active", False) and not all(_is_public(i[4][0]) for i in infos):
            raise socket.gaierror(socket.EAI_NONAME, "forbidden_host")
        return infos

    getaddrinfo.aivps_dns_guard = True
    return getaddrinfo


@contextmanager
def public_only_dns():
    """Inside the block, every DNS lookup made by the current thread must
    resolve to public addresses only. Other threads (DB, Redis, uvicorn
    workers) are unaffected: the wrapper only enforces while the thread-local
    flag is set."""
    if not getattr(socket.getaddrinfo, "aivps_dns_guard", False):
        socket.getaddrinfo = _guarded(socket.getaddrinfo)
    _dns_guard.active = True
    try:
        yield
    finally:
        _dns_guard.active = False


def validate_url(url: str) -> str:
    """Return the stripped URL or raise UrlSourceError("invalid_url" | "forbidden_host")."""
    url = (url or "").strip()
    if not url or len(url) > _MAX_URL_LENGTH or any(ch.isspace() for ch in url):
        raise UrlSourceError("invalid_url")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UrlSourceError("invalid_url")
    if parts.username or parts.password:
        raise UrlSourceError("invalid_url")  # credentials would leak into the package
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
        # keep HLS on the native downloader: ffmpeg would resolve hosts itself,
        # outside public_only_dns()
        "hls_prefer_native": True,
    }


def pick_manual_subtitle_lang(available: list[str], language: str | None) -> str | None:
    """Pick the author's subtitle track in the video's language, if any.

    Exact code first ("ru"), then a regional variant ("ru-RU"). Never a track
    in another language: that would be a translation, not the speech. Codes
    come from the site, so anything but [A-Za-z0-9-] is ignored.
    """
    if not language:
        return None
    language = language.lower()
    candidates = [code for code in available if _SUBTITLE_LANG.match(code)]
    for code in candidates:
        if code.lower() == language:
            return code
    for code in candidates:
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
    webpage_url = info.get("webpage_url")
    if webpage_url and urlsplit(str(webpage_url)).scheme not in ("http", "https"):
        webpage_url = None
    return UrlProbe(
        title=str(info.get("title") or "video").strip() or "video",
        duration=float(duration),
        language=(info.get("language") or None),
        manual_subtitle_langs=manual,
        chapters=chapters,
        channel=info.get("channel") or info.get("uploader"),
        webpage_url=webpage_url,
        description=info.get("description"),
    )


def probe_url(url: str) -> UrlProbe:
    """Read metadata without downloading. Raises UrlSourceError."""
    import yt_dlp

    try:
        with public_only_dns(), yt_dlp.YoutubeDL({**_base_options(), "skip_download": True}) as ydl:
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

    video_path is a trusted UUID-named path under upload_dir. Every file with
    its stem except the final video is removed on success and on any error.
    """
    import yt_dlp

    validate_url(url)  # DNS may have changed since the job was submitted
    stem = video_path.stem
    work_dir = video_path.parent

    def _cleanup_partials() -> None:
        for leftover in work_dir.glob(f"{stem}.*"):
            if leftover != video_path:
                leftover.unlink(missing_ok=True)

    written: dict[str, int] = {}

    def _enforce_size(progress: dict) -> None:
        written[str(progress.get("filename"))] = int(progress.get("downloaded_bytes") or 0)
        if sum(written.values()) > max_bytes:
            raise yt_dlp.utils.DownloadCancelled("file_too_large")

    try:
        probe = probe_url(url)
        sub_lang = pick_manual_subtitle_lang(probe.manual_subtitle_langs, language or probe.language)
        options = {
            **_base_options(),
            "format": _FORMAT,
            "merge_output_format": "mp4",
            "outtmpl": {"default": str(work_dir / f"{stem}.%(ext)s")},
            "max_filesize": max_bytes,
            "progress_hooks": [_enforce_size],
            "writesubtitles": sub_lang is not None,
            "writeautomaticsub": False,
            "subtitleslangs": [re.escape(sub_lang)] if sub_lang else [],
            "subtitlesformat": "vtt",
            "retries": 3,
        }
        try:
            with public_only_dns(), yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=True)
        except (yt_dlp.utils.DownloadError, yt_dlp.utils.DownloadCancelled) as exc:
            logger.info("url_download_failed", stage="downloading", error=str(exc)[:300])
            raise UrlSourceError("file_too_large" if "file_too_large" in str(exc) else "download_failed")

        produced = sorted(
            (p for p in work_dir.glob(f"{stem}.*") if p.suffix not in (".vtt", ".part", ".ytdl")),
            key=lambda p: p.suffix != ".mp4",
        )
        if not produced:
            raise UrlSourceError("download_failed")
        produced[0].replace(video_path)
        if video_path.stat().st_size > max_bytes:
            video_path.unlink(missing_ok=True)
            raise UrlSourceError("file_too_large")

        subtitle_path = None
        if sub_lang:
            vtt = work_dir / f"{stem}.{sub_lang}.vtt"
            if vtt.exists():
                subtitles_dir.mkdir(parents=True, exist_ok=True)
                subtitle_path = subtitles_dir / f"subtitles.{sub_lang}.vtt"
                shutil.move(str(vtt), subtitle_path)  # upload_dir and output_dir may be different mounts
        if subtitle_path is None:
            sub_lang = None
        return UrlDownload(video_path=video_path, subtitle_path=subtitle_path, subtitle_lang=sub_lang, probe=probe)
    finally:
        _cleanup_partials()
