"""Run the app on a real port and talk to it like an MCP client does."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from mac_api.app import create_app
from mac_api.config import Settings

API_KEY = "test-key"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(settings_or_app) -> Iterator[str]:
    app = create_app(settings_or_app) if isinstance(settings_or_app, Settings) else settings_or_app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def call_tools(url: str, calls: list[tuple[str, dict]], token: str = API_KEY) -> tuple[list[str], list]:
    """Connect like a real MCP client; return the tool names and the result of each call."""

    async def run() -> tuple[list[str], list]:
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with Client(streamable_http_client(f"{url}/mcp", http_client=http)) as client:
            tools = [tool.name for tool in (await client.list_tools()).tools]
            results = [await client.call_tool(name, arguments) for name, arguments in calls]
        return tools, results

    return asyncio.run(run())


def text(result) -> str:
    return "".join(part.text for part in result.content if part.type == "text")
