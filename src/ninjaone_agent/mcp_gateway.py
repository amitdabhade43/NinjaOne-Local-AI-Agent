import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ninjaone_agent.config import Settings


class MCPGatewayError(RuntimeError):
    """MCP gateway connection or protocol error."""


class NinjaOneMCPGateway:
    """Launches the local NinjaOne MCP gateway as a STDIO subprocess."""

    def __init__(self, settings: Settings):
        self.gateway_dir = Path(settings.ninjaone_gateway_dir)
        self.python = Path(settings.ninjaone_gateway_python)
        self.env_file = Path(settings.ninjaone_gateway_env_file)

    def _gateway_environment(self) -> dict[str, str]:
        """Load gateway configuration without logging or exposing credentials."""
        if not self.env_file.is_file():
            raise MCPGatewayError(
                f"Gateway environment file was not found: {self.env_file}"
            )

        # Preserve Windows system variables (especially PATH) and let explicit
        # process environment variables override values from the gateway .env.
        env = os.environ.copy()
        file_values = dotenv_values(self.env_file)
        for key, value in file_values.items():
            if value is not None and key not in env:
                env[key] = value
        return env

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        try:
            if not self.gateway_dir.is_dir():
                raise MCPGatewayError(
                    f"Gateway directory was not found: {self.gateway_dir}"
                )
            if not self.python.is_file():
                raise MCPGatewayError(
                    f"Gateway Python executable was not found: {self.python}"
                )

            server = StdioServerParameters(
                command=str(self.python),
                args=["-m", "ninja_mcp"],
                env=self._gateway_environment(),
                cwd=str(self.gateway_dir),
            )

            async with stdio_client(server) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session

        except MCPGatewayError:
            raise
        except Exception as exc:
            raise MCPGatewayError(
                f"Could not connect to NinjaOne MCP gateway: {exc}"
            ) from exc

    async def list_tools(self) -> list[dict[str, Any]]:
        async with self.session() as session:
            result = await session.list_tools()
            return [self._tool_to_dict(tool) for tool in result.tools]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.session() as session:
            result = await session.call_tool(name, arguments)
            content = []
            for item in result.content or []:
                if hasattr(item, "text"):
                    content.append(item.text)
                else:
                    content.append(str(item))
            return {
                "is_error": bool(result.isError),
                "content": content,
                "structured_content": getattr(
                    result, "structuredContent", None
                ),
            }

    @staticmethod
    def _tool_to_dict(tool: Any) -> dict[str, Any]:
        schema = getattr(tool, "inputSchema", None) or {
            "type": "object",
            "properties": {},
        }
        annotations = getattr(tool, "annotations", None)
        return {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": schema,
            "read_only_hint": (
                getattr(annotations, "readOnlyHint", None)
                if annotations else None
            ),
            "destructive_hint": (
                getattr(annotations, "destructiveHint", None)
                if annotations else None
            ),
        }
