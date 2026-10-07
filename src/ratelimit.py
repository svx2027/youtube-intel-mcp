"""In-memory rate limiting for the Streamable HTTP transport.

Stdio has no network boundary - it is one local subprocess talking to one
local client over pipes with nothing in between, so there is no "client" to
throttle and no DoS surface to defend; this module is never wired in for
that transport (see server.py). It only applies when the server is
reachable over a socket (--transport streamable-http), where an unbounded
endpoint is a real resource-exhaustion vector, same class of risk as the
unbounded-batch problem src/validation.py defends against, one layer up the
stack (per request, not per field).

Fixed-window counter per client IP: dependency-free (stdlib only, no new
requirements.txt entry) and enough for a scaffold server with no auth ahead
of it. Not a production-grade limiter - single-process, in-memory state
(resets on restart, not shared across workers, trusts scope["client"]
as-is with no X-Forwarded-For handling, so it is only meaningful run
directly, not behind a proxy that would need its own real-IP handling
first). See README "Honest scope" for what deploying this behind something
sturdier (nginx/Cloudflare rate limiting, an API gateway) would replace.
"""
from __future__ import annotations

import json
import time
from threading import Lock
from typing import Any, Awaitable, Callable

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class FixedWindowRateLimiter:
    """Allows at most `max_requests` per `window_seconds`, per key.

    A fixed (not sliding) window: simple, O(1) per check, and the one
    tradeoff worth naming - a key can burst up to 2x max_requests across a
    window boundary (max_requests at the tail of one window, max_requests
    again at the head of the next). Acceptable for a scaffold server
    defending against sustained flooding, not for billing-grade metering.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = Lock()
        self._windows: dict[str, tuple[int, int]] = {}  # key -> (window_index, count)

    def allow(self, key: str, now: float | None = None) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds); retry_after is 0.0 when allowed."""
        now = time.monotonic() if now is None else now
        window = int(now // self.window_seconds)
        with self._lock:
            start, count = self._windows.get(key, (window, 0))
            if start != window:
                start, count = window, 0
            count += 1
            self._windows[key] = (start, count)
            if count > self.max_requests:
                window_end = (start + 1) * self.window_seconds
                return False, max(window_end - now, 0.0)
            return True, 0.0


class RateLimitASGIMiddleware:
    """Wraps an ASGI app; rejects over-limit HTTP requests with a 429.

    Only "http" scope requests are metered - "lifespan" (startup/shutdown)
    always passes straight through untouched, and there is no other scope
    type Streamable HTTP uses.
    """

    def __init__(self, app: Callable, limiter: FixedWindowRateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        key = client[0] if client else "unknown"
        allowed, retry_after = self.limiter.allow(key)
        if allowed:
            await self.app(scope, receive, send)
            return
        body = json.dumps({
            "error": "rate_limited",
            "message": f"Rate limit exceeded ({self.limiter.max_requests} requests "
                       f"per {self.limiter.window_seconds:.0f}s per client).",
            "retry_after_seconds": round(retry_after, 1),
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", str(int(retry_after) + 1).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
