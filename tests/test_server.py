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


if __name__ == "__main__":
    unittest.main()
