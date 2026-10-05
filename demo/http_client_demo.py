"""Recorded-transcript demo: a real MCP client calling this server over
Streamable HTTP, end to end.

What this proves, that reading server.py cannot: the server actually speaks
the Streamable HTTP transport (session initialize, tool listing, and a real
tool call all round-tripping over HTTP), not just that --transport is a
recognized flag.

This script does NOT start the server itself -- run it against a server you
already started, so the transcript it prints is a real client/server
exchange over a real socket, not two halves of one process talking in
memory:

    # terminal 1
    python3 server.py --transport streamable-http --port 8000

    # terminal 2
    python3 demo/http_client_demo.py

See demo/session_transcript.txt for a captured run of exactly this script
against exactly that server command, saved so a reader doesn't need to spin
up their own server just to see it work.
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = "http://127.0.0.1:8000/mcp"

SAMPLE_CANDIDATES = [
    {"title": "Best kettlebell for beginners", "vph": 12.0,
     "channel_id": "c1", "relation": "direct"},
    {"title": "Kettlebell workout for beginners", "vph": 400.0,
     "channel_id": "c2", "relation": "direct"},
    {"title": "5 resistance band mistakes everyone makes", "vph": 55.0,
     "channel_id": "c2", "relation": "direct"},
]


async def main() -> None:
    print(f"Connecting to {SERVER_URL} ...")
    async with streamablehttp_client(SERVER_URL) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            print(f"Initialized session with server: "
                  f"{init_result.serverInfo.name} {init_result.serverInfo.version}")

            tools_result = await session.list_tools()
            tool_names = sorted(t.name for t in tools_result.tools)
            print(f"Server advertises {len(tool_names)} tools: {tool_names}")

            print("\nCalling score_candidates with 3 sample candidates ...")
            call_result = await session.call_tool(
                "score_candidates", {"candidates": SAMPLE_CANDIDATES},
            )
            payload = json.loads(call_result.content[0].text)
            print(f"Got {payload['meta']['n_candidates']} scored candidates back, "
                  f"ranked by opportunity_score:")
            for c in payload["candidates"]:
                print(f"  {c['opportunity_score']:>6.2f}  {c['title']}")

    print("\nSession closed cleanly. This was a real HTTP round trip, not an "
          "in-process call.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConnectionError as exc:
        print(f"Could not reach {SERVER_URL}: {exc}", file=sys.stderr)
        print("Start the server first: python3 server.py --transport "
              "streamable-http --port 8000", file=sys.stderr)
        sys.exit(1)
