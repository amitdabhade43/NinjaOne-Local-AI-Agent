from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class MCPGatewayError(RuntimeError):
    """MCP gateway connection or protocol error."""


class NinjaOneMCPGateway:
    """Connects to the separately hosted MCP server using Streamable HTTP."""

    def __init__(self, url: str):
        self.url = url

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        try:
            async with streamablehttp_client(self.url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
        except Exception as exc:
            raise MCPGatewayError(f"Could not connect to NinjaOne MCP gateway at {self.url}: {exc}") from exc

    async def list_tools(self) -> list[dict[str, Any]]:
        async with self.session() as session:
            result = await session.list_tools()
            return [self._tool_to_dict(tool) for tool in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self.session() as session:
            result = await session.call_tool(name, arguments)
            content = []
            for item in (result.content or []):
                if hasattr(item, "text"):
                    content.append(item.text)
                else:
                    content.append(str(item))
            return {
                "is_error": bool(result.isError),
                "content": content,
                "structured_content": getattr(result, "structuredContent", None),
            }

    @staticmethod
    def _tool_to_dict(tool: Any) -> dict[str, Any]:
        schema = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
        annotations = getattr(tool, "annotations", None)
        return {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": schema,
            "read_only_hint": getattr(annotations, "readOnlyHint", None) if annotations else None,
            "destructive_hint": getattr(annotations, "destructiveHint", None) if annotations else None,
        }
