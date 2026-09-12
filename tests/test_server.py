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


if __name__ == "__main__":
    unittest.main()
