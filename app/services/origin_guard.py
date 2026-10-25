"""Reject cross-site state-changing requests (CSRF), ADR 006.

Browsers attach an Origin header to cross-site POSTs. A POST/PUT/PATCH/DELETE
whose Origin (scheme + host + port) is not in settings.allowed_origins, or
that a browser marks Sec-Fetch-Site: cross-site, gets 403 before it reaches
any route. Requests without Origin (curl, tests) pass: the CSRF vector is a
browser, and browsers always send it on these methods.

Pure ASGI (not BaseHTTPMiddleware) so multi-GB upload bodies keep streaming.
"""

import json

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class OriginGuardMiddleware:
    def __init__(self, app, allowed_origins: list[str]):
        self.app = app
        self.allowed_origins = {o.lower().rstrip("/") for o in allowed_origins}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in _SAFE_METHODS:
            headers = dict(scope.get("headers", []))
            origin = headers.get(b"origin")
            cross_site = headers.get(b"sec-fetch-site") == b"cross-site"
            if cross_site or (origin is not None and not self._allowed(origin.decode("latin-1"))):
                body = json.dumps({"detail": "cross_origin_rejected"}).encode()
                await send({
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)

    def _allowed(self, origin: str) -> bool:
        return origin.lower().rstrip("/") in self.allowed_origins
