"""Smoke tests: the MCP server imports cleanly, its tools are the plain
functions they wrap (not just decorated placeholders), and a real call goes
through the actual MCP dispatch path (FastMCP's call_tool), not just a
direct Python import - so "registers a tool named X" and "that tool
actually runs when invoked the way an MCP client invokes it" are both
checked, not assumed from each other.
"""
import asyncio
import json
import unittest

from mcp.server.fastmcp.exceptions import ToolError


class TestServerImports(unittest.TestCase):
    def test_server_module_imports_and_registers_tools(self):
        import server
        self.assertEqual(server.mcp.name, "youtube-intel-mcp")

    def test_tool_functions_are_callable_directly(self):
        # @mcp.tool() returns the original function unchanged in this SDK
        # version, so every tool doubles as a plain, directly unit-testable
        # Python function with no MCP transport involved.
        import server
        result = server.score_candidates([
            {"title": "a", "vph": 10.0, "channel_id": "c1", "relation": "direct"},
        ])
        self.assertEqual(result["meta"]["n_candidates"], 1)

    def test_registered_tools_match_expected_names(self):
        import server
        names = {t.name for t in server.mcp._tool_manager.list_tools()}
        self.assertEqual(
            names, {"score_candidates", "compute_demand_gap", "tag_topics", "cluster_titles"})

    def test_call_tool_through_real_mcp_dispatch(self):
        # Goes through FastMCP's own call_tool, the same path a real MCP
        # client uses, not a direct function call - proves the tool is
        # actually invokable as an MCP tool, not just present in the list.
        import server

        async def _call():
            return await server.mcp.call_tool("score_candidates", {
                "candidates": [
                    {"title": "a", "vph": 10.0, "channel_id": "c1", "relation": "direct"},
                    {"title": "b", "vph": 100.0, "channel_id": "c1", "relation": "direct"},
                ],
            })

        content, structured = asyncio.run(_call())
        self.assertTrue(content)
        payload = json.loads(content[0].text)
        self.assertEqual(payload["meta"]["n_candidates"], 2)
        self.assertEqual(structured["meta"]["n_candidates"], 2)


class TestToolValidation(unittest.TestCase):
    """Each tool validates its own input before touching src/scoring.py,
    src/demand.py, or src/taxonomy.py (see src/validation.py) - confirms
    that wiring is live on the actual tool functions, not just unit-tested
    against validation.py in isolation (tests/test_validation.py)."""

    def test_score_candidates_rejects_malformed_batch(self):
        import server
        with self.assertRaisesRegex(ValueError, "candidates must be a list"):
            server.score_candidates("not a list")

    def test_score_candidates_rejects_bad_field_type(self):
        import server
        with self.assertRaisesRegex(ValueError, r"candidates\[0\].vph must be a number"):
            server.score_candidates([{"vph": "fast"}])

    def test_compute_demand_gap_rejects_bad_keywords(self):
        import server
        with self.assertRaisesRegex(ValueError, "keywords must be a list"):
            server.compute_demand_gap([], "not a list")

    def test_tag_topics_rejects_bad_taxonomy(self):
        import server
        with self.assertRaisesRegex(ValueError, r"taxonomy_labels\[0\].label"):
            server.tag_topics([], [{"hints": ["x"]}])

    def test_cluster_titles_rejects_bad_max_df(self):
        import server
        with self.assertRaisesRegex(ValueError, "max_df must be between 0 and 1"):
            server.cluster_titles(["a"], max_df=2.0)

    def test_malformed_input_surfaces_as_clean_tool_error_over_mcp_dispatch(self):
        # Same malformed-input case as above, but routed through the real
        # MCP call_tool path (see test_call_tool_through_real_mcp_dispatch
        # above) - confirms a client gets a ToolError naming the bad field,
        # not a raw traceback from deep inside scoring.py.
        import server

        async def _call():
            return await server.mcp.call_tool("score_candidates", {
                "candidates": [{"vph": "fast"}],
            })

        with self.assertRaisesRegex(ToolError, r"candidates\[0\].vph must be a number"):
            asyncio.run(_call())


class TestTransportCli(unittest.TestCase):
    """--transport is the knob between the default stdio path (what
    test_call_tool_through_real_mcp_dispatch above exercises) and Streamable
    HTTP (exercised for real, over an actual socket, in
    demo/http_client_demo.py - see demo/session_transcript.txt for a
    captured run). These tests cover the CLI parsing and settings wiring
    without binding a port, since mcp.run() blocks forever once started.
    """

    def test_default_transport_is_stdio(self):
        import sys
        import server
        # Parse with no argv at all, the real default path.
        old_argv = sys.argv
        try:
            sys.argv = ["server.py"]
            args = server._parse_args()
        finally:
            sys.argv = old_argv
        self.assertEqual(args.transport, "stdio")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)

    def test_streamable_http_flags_parse(self):
        import sys
        import server
        old_argv = sys.argv
        try:
            sys.argv = ["server.py", "--transport", "streamable-http",
                        "--host", "0.0.0.0", "--port", "9100"]
            args = server._parse_args()
        finally:
            sys.argv = old_argv
        self.assertEqual(args.transport, "streamable-http")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9100)

    def test_apply_transport_args_sets_settings_only_for_network_transport(self):
        import argparse
        import server

        fresh = server.FastMCP("test-apply")
        stdio_args = argparse.Namespace(transport="stdio", host="9.9.9.9", port=1)
        server._apply_transport_args(fresh, stdio_args)
        # stdio ignores host/port entirely - no reason to mutate settings
        # a stdio run will never read.
        self.assertEqual(fresh.settings.host, "127.0.0.1")
        self.assertEqual(fresh.settings.port, 8000)

        http_args = argparse.Namespace(transport="streamable-http", host="0.0.0.0", port=9100)
        server._apply_transport_args(fresh, http_args)
        self.assertEqual(fresh.settings.host, "0.0.0.0")
        self.assertEqual(fresh.settings.port, 9100)

    def test_streamable_http_app_exposes_mcp_route(self):
        # No socket, no network: this just confirms the ASGI app the real
        # server would serve under --transport streamable-http actually
        # mounts the /mcp path the README and demo/http_client_demo.py
        # both point clients at.
        import server
        app = server.mcp.streamable_http_app()
        paths = {getattr(r, "path", None) for r in app.routes}
        self.assertIn(server.mcp.settings.streamable_http_path, paths)

    def test_rate_limit_flags_have_sane_defaults(self):
        import sys
        import server
        old_argv = sys.argv
        try:
            sys.argv = ["server.py"]
            args = server._parse_args()
        finally:
            sys.argv = old_argv
        self.assertEqual(args.rate_limit_max_requests, 120)
        self.assertEqual(args.rate_limit_window_seconds, 60.0)
        self.assertFalse(args.disable_rate_limit)

    def test_rate_limit_flags_parse(self):
        import sys
        import server
        old_argv = sys.argv
        try:
            sys.argv = ["server.py", "--transport", "streamable-http",
                        "--rate-limit-max-requests", "5",
                        "--rate-limit-window-seconds", "10", "--disable-rate-limit"]
            args = server._parse_args()
        finally:
            sys.argv = old_argv
        self.assertEqual(args.rate_limit_max_requests, 5)
        self.assertEqual(args.rate_limit_window_seconds, 10.0)
        self.assertTrue(args.disable_rate_limit)


class TestBuildHttpApp(unittest.TestCase):
    """_build_http_app is what --transport streamable-http actually serves
    (see server._run_streamable_http) - these confirm the rate limiter is
    wired in by default and removable by flag, without binding a socket.
    """

    def _args(self, disable_rate_limit=False, max_requests=120, window_seconds=60.0):
        import argparse
        return argparse.Namespace(
            disable_rate_limit=disable_rate_limit,
            rate_limit_max_requests=max_requests,
            rate_limit_window_seconds=window_seconds,
        )

    def test_default_wraps_app_in_rate_limiter(self):
        import server
        from src import ratelimit
        app = server._build_http_app(server.mcp, self._args())
        self.assertIsInstance(app, ratelimit.RateLimitASGIMiddleware)

    def test_disable_rate_limit_returns_bare_app(self):
        import server
        from src import ratelimit
        app = server._build_http_app(server.mcp, self._args(disable_rate_limit=True))
        self.assertNotIsInstance(app, ratelimit.RateLimitASGIMiddleware)

    def test_wrapped_app_still_exposes_mcp_route(self):
        # The rate limiter wraps the ASGI app; it must not hide the /mcp
        # route from the underlying Starlette app it wraps.
        import server
        app = server._build_http_app(server.mcp, self._args())
        paths = {getattr(r, "path", None) for r in app.app.routes}
        self.assertIn(server.mcp.settings.streamable_http_path, paths)


if __name__ == "__main__":
    unittest.main()
