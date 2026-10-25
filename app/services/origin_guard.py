"""Reject cross-site state-changing requests (CSRF), ADR 006.

Browsers attach an Origin header to cross-site POSTs. A POST/PUT/PATCH/DELETE
whose Origin host is not one of settings.allowed_hosts gets 403 before it
reaches any route. Requests without Origin (curl, tests) pass: the CSRF vector
is a browser, and browsers always send it on these methods.

Pure ASGI (not BaseHTTPMiddleware) so multi-GB upload bodies keep streaming.
"""

import json
from urllib.parse import urlsplit

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class OriginGuardMiddleware:
    def __init__(self, app, allowed_hosts: list[str]):
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in _SAFE_METHODS:
            origin = None
            for name, value in scope.get("headers", []):
                if name == b"origin":
                    origin = value.decode("latin-1")
                    break
            if origin is not None and not self._allowed(origin):
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
        if origin == "null":
            return False
        host = urlsplit(origin).hostname
        return host is not None and host.lower() in self.allowed_hosts
