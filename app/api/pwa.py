"""Installable web app (PWA) bits: manifest + favicon.

Lets Chrome / Edge install the local UI as a standalone app window
(address-bar "Install" icon). No service worker: the app is useless
offline (needs the local stack), and caching would only hide a dead stack.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

STATIC_DIR = Path(__file__).parent.parent / "static"

router = APIRouter(tags=["pwa"])

_MANIFEST = {
    "name": "AI Video Prep Studio",
    "short_name": "Video Prep",
    "description": "Видео → ZIP для AI-агента: транскрипт с таймкодами, кадры, главы.",
    "lang": "ru",
    "id": "/",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f9fafb",
    "theme_color": "#2563eb",
    "icons": [
        {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}


@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> JSONResponse:
    return JSONResponse(_MANIFEST, media_type="application/manifest+json")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon-32.png", media_type="image/png")
