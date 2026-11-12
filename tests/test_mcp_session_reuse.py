import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from ninjaone_agent.mcp_gateway import NinjaOneMCPGateway


class FakeSession:
    def __init__(self):
        self.list_calls = 0
        self.tool_calls = []

    async def list_tools(self):
        self.list_calls += 1
        return SimpleNamespace(tools=[])

    async def call_tool(self, name, arguments):
        self.tool_calls.append((name, arguments))
        return SimpleNamespace(content=[], isError=False, structuredContent={"ok": True})


def test_connection_reuses_one_stdio_session_for_multiple_tool_calls(monkeypatch):
    async def scenario():
    gateway = object.__new__(NinjaOneMCPGateway)
    gateway._active_session = ContextVar("test_mcp_session", default=None)
    fake = FakeSession()
    opened = 0

    @asynccontextmanager
    async def fake_session():
        nonlocal opened
        opened += 1
        yield fake

    monkeypatch.setattr(gateway, "session", fake_session)

    async with gateway.connection():
        assert await gateway.list_tools() == []
        result = await gateway.call_tool("discover_endpoints", {"intent": "devices"})
        assert result["structured_content"] == {"ok": True}

    assert opened == 1

    asyncio.run(scenario())
    assert fake.list_calls == 1
    assert fake.tool_calls == [("discover_endpoints", {"intent": "devices"})]

    asyncio.run(scenario())


def test_tool_call_outside_connection_opens_its_own_session(monkeypatch):
    async def scenario():
    gateway = object.__new__(NinjaOneMCPGateway)
    gateway._active_session = ContextVar("test_mcp_session_outside", default=None)
    fake = FakeSession()
    opened = 0

    @asynccontextmanager
    async def fake_session():
        nonlocal opened
        opened += 1
        yield fake

    monkeypatch.setattr(gateway, "session", fake_session)
    await gateway.call_tool("inspect_endpoint", {"endpoint": "GET /v2/devices"})
    assert opened == 1
