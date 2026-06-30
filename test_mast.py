import asyncio
import json
import os
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def test_mast():
    # Setup server params
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["mcp_servers/mast_archive/server.py"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("Testing search_observations with TRAPPIST-1")
            result1 = await session.call_tool("search_observations", arguments={"target": "TRAPPIST-1", "limit": 2})
            print("\nResult 1:")
            print(result1.content[0].text)

if __name__ == "__main__":
    asyncio.run(test_mast())
