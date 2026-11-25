"""Browser safety checks for the token-free local demo.

The loopback port binding is the network boundary. Host and Origin checks
mitigate DNS rebinding and cross-origin browser requests; they are not identity.
"""
from __future__ import annotations

import re

from aiohttp import web

LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?", re.IGNORECASE)


@web.middleware
async def local_management(request: web.Request, handler):
    if request.path in {"/admin", "/pulse"} or request.path.startswith(("/admin/", "/pulse/")):
        host = request.headers.get("Host", "")
        origin = request.headers.get("Origin")
        if not LOCAL_HOST.fullmatch(host) or (
            origin is not None and origin != f"{request.scheme}://{host}"
        ) or request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise web.HTTPForbidden(text="Local management only")
        if request.path.startswith("/admin/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("X-Pulse-Local") != "1":
                raise web.HTTPForbidden(text="Local management header required")
    return await handler(request)
