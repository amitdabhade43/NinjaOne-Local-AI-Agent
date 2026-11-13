import asyncio

import pytest

from ninjaone_agent.mcp_gateway import MCPGatewayError, NinjaOneMCPGateway


class HangingSession:
    async def list_tools(self):
        await asyncio.sleep(1)

    async def call_tool(self, name, arguments):
        await asyncio.sleep(1)


def _gateway(timeout=0.01):
    gateway = object.__new__(NinjaOneMCPGateway)
    gateway.operation_timeout_seconds = timeout
    return gateway


def test_mcp_tool_call_has_a_bounded_timeout():
    async def scenario():
        gateway = _gateway()
        with pytest.raises(MCPGatewayError, match="timed out"):
            await gateway._call_tool_on(HangingSession(), "discover_endpoints", {"intent": "devices"})

    asyncio.run(scenario())


def test_mcp_tool_discovery_has_a_bounded_timeout():
    async def scenario():
        gateway = _gateway()
        with pytest.raises(MCPGatewayError, match="timed out"):
            await gateway._list_tools_from(HangingSession())

    asyncio.run(scenario())
