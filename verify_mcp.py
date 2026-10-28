import asyncio
import os
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    url = os.environ.get("MCP_URL", "http://127.0.0.1:9330/mcp")
    token = os.environ.get("MCP_TOKEN", "test-local-token")
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            read, write, _ = streams
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                status = await session.call_tool("cloud_status", {})
                print({
                    "server": init.serverInfo.name,
                    "tools": [tool.name for tool in tools.tools],
                    "status_is_error": status.isError,
                    "status_content_items": len(status.content),
                })


asyncio.run(main())
