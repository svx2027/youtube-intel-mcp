"""src/ratelimit.py: the fixed-window limiter and ASGI middleware that
guard the Streamable HTTP transport. No real socket or uvicorn involved -
FixedWindowRateLimiter is tested as plain Python, and
RateLimitASGIMiddleware is driven with hand-built ASGI scope/receive/send
callables, the same technique Starlette's own TestClient uses underneath,
without pulling in that dependency just for this."""
import asyncio
import unittest

from src import ratelimit


class TestFixedWindowRateLimiter(unittest.TestCase):
    def test_rejects_non_positive_max_requests(self):
        with self.assertRaises(ValueError):
            ratelimit.FixedWindowRateLimiter(0, 60)

    def test_rejects_non_positive_window(self):
        with self.assertRaises(ValueError):
            ratelimit.FixedWindowRateLimiter(10, 0)

    def test_allows_up_to_max_within_window(self):
        limiter = ratelimit.FixedWindowRateLimiter(3, 60)
        for _ in range(3):
            allowed, retry_after = limiter.allow("1.2.3.4", now=10.0)
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0.0)

    def test_blocks_once_over_max_in_same_window(self):
        limiter = ratelimit.FixedWindowRateLimiter(2, 60)
        limiter.allow("1.2.3.4", now=10.0)
        limiter.allow("1.2.3.4", now=10.0)
        allowed, retry_after = limiter.allow("1.2.3.4", now=10.0)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0.0)

    def test_resets_in_a_new_window(self):
        limiter = ratelimit.FixedWindowRateLimiter(1, 60)
        self.assertTrue(limiter.allow("1.2.3.4", now=0.0)[0])
        self.assertFalse(limiter.allow("1.2.3.4", now=1.0)[0])
        # 61s later is a fresh window - the count resets.
        self.assertTrue(limiter.allow("1.2.3.4", now=61.0)[0])

    def test_keys_are_independent(self):
        limiter = ratelimit.FixedWindowRateLimiter(1, 60)
        self.assertTrue(limiter.allow("1.2.3.4", now=0.0)[0])
        # A different key has its own budget, unaffected by the first.
        self.assertTrue(limiter.allow("5.6.7.8", now=0.0)[0])
        self.assertFalse(limiter.allow("1.2.3.4", now=0.0)[0])


class _RecordingSend:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)


async def _empty_receive():
    return {"type": "http.request"}


async def _run_app(app, scope):
    send = _RecordingSend()
    await app(scope, _empty_receive, send)
    return send.messages


def _http_scope(client_ip="1.2.3.4"):
    return {"type": "http", "client": (client_ip, 12345), "method": "GET", "path": "/mcp"}


class TestRateLimitASGIMiddleware(unittest.TestCase):
    def _passthrough_app(self, calls):
        async def app(scope, receive, send):
            calls.append(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        return app

    def test_passes_through_lifespan_scope_untouched(self):
        calls = []
        limiter = ratelimit.FixedWindowRateLimiter(1, 60)
        middleware = ratelimit.RateLimitASGIMiddleware(self._passthrough_app(calls), limiter)
        messages = asyncio.run(_run_app(middleware, {"type": "lifespan"}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(messages[0]["status"], 200)

    def test_allows_requests_within_limit(self):
        calls = []
        limiter = ratelimit.FixedWindowRateLimiter(2, 60)
        middleware = ratelimit.RateLimitASGIMiddleware(self._passthrough_app(calls), limiter)
        asyncio.run(_run_app(middleware, _http_scope()))
        asyncio.run(_run_app(middleware, _http_scope()))
        self.assertEqual(len(calls), 2)

    def test_blocks_with_429_once_over_limit(self):
        calls = []
        limiter = ratelimit.FixedWindowRateLimiter(1, 60)
        middleware = ratelimit.RateLimitASGIMiddleware(self._passthrough_app(calls), limiter)
        asyncio.run(_run_app(middleware, _http_scope()))
        messages = asyncio.run(_run_app(middleware, _http_scope()))
        self.assertEqual(len(calls), 1)  # second request never reached the app
        start = next(m for m in messages if m["type"] == "http.response.start")
        self.assertEqual(start["status"], 429)
        headers = dict(start["headers"])
        self.assertIn(b"retry-after", headers)
        body = next(m for m in messages if m["type"] == "http.response.body")
        self.assertIn(b"rate_limited", body["body"])

    def test_different_client_ips_tracked_separately(self):
        calls = []
        limiter = ratelimit.FixedWindowRateLimiter(1, 60)
        middleware = ratelimit.RateLimitASGIMiddleware(self._passthrough_app(calls), limiter)
        asyncio.run(_run_app(middleware, _http_scope("1.1.1.1")))
        asyncio.run(_run_app(middleware, _http_scope("2.2.2.2")))
        self.assertEqual(len(calls), 2)

    def test_missing_client_falls_back_to_unknown_key(self):
        calls = []
        limiter = ratelimit.FixedWindowRateLimiter(1, 60)
        middleware = ratelimit.RateLimitASGIMiddleware(self._passthrough_app(calls), limiter)
        scope = {"type": "http", "client": None, "method": "GET", "path": "/mcp"}
        messages = asyncio.run(_run_app(middleware, scope))
        self.assertEqual(len(calls), 1)
        start = next(m for m in messages if m["type"] == "http.response.start")
        self.assertEqual(start["status"], 200)


if __name__ == "__main__":
    unittest.main()
