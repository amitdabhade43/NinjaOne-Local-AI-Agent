from ninjaone_agent.agent import NinjaOneAgent, _direct_inventory_intent


def _tool(name, properties, required):
    return {
        "name": name,
        "description": name,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


class FakeCatalog:
    def get(self, path, method="GET"):
        if path == "/v2/organizations" and method == "GET":
            return {"path": path, "method": method}
        return None


class FakeGateway:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "inspect_endpoint":
            return {
                "is_error": False,
                "structured_content": {
                    "method": "GET",
                    "path": "/v2/organizations",
                },
            }
        if name == "execute_endpoint":
            return {
                "is_error": False,
                "structured_content": {
                    "executed": True,
                    "complete": True,
                    "truncated": False,
                    "data": [{"id": 1, "name": "Org A"}, {"id": 2, "name": "Org B"}],
                },
            }
        raise AssertionError(f"Unexpected MCP tool: {name}")


def test_direct_inventory_intent_accepts_platform_suffix():
    assert _direct_inventory_intent(
        "How many organizations are in NinjaOne?"
    ) == {"resource": "organizations", "operation": "count"}


def test_schema_inventory_uses_inspect_then_execute_for_count():
    async def scenario():
        agent = object.__new__(NinjaOneAgent)
        agent.api_catalog = FakeCatalog()
        agent.gateway = FakeGateway()
        tools = {
            "inspect_endpoint": _tool("inspect_endpoint", {"endpoint": {"type": "string"}}, ["endpoint"]),
            "execute_endpoint": _tool(
                "execute_endpoint",
                {
                    "endpoint": {"type": "string"},
                    "path_params": {"type": "object"},
                    "query": {"type": "object"},
                    "body": {"type": "object"},
                },
                ["endpoint"],
            ),
        }

        result = await agent._run_schema_inventory(
            {"resource": "organizations", "operation": "count"},
            tools,
            "qwen3-14b",
        )

        assert result["answer"] == "NinjaOne returned 2 organizations record(s)."
        assert [name for name, _ in agent.gateway.calls] == [
            "inspect_endpoint",
            "execute_endpoint",
        ]

    import asyncio
    asyncio.run(scenario())


def test_schema_inventory_does_not_claim_complete_count_for_truncated_data():
    async def scenario():
        agent = object.__new__(NinjaOneAgent)
        agent.api_catalog = FakeCatalog()

        class TruncatedGateway(FakeGateway):
            async def call_tool(self, name, arguments):
                if name == "inspect_endpoint":
                    return await super().call_tool(name, arguments)
                self.calls.append((name, arguments))
                return {
                    "is_error": False,
                    "structured_content": {
                        "executed": True,
                        "complete": False,
                        "truncated": True,
                        "data": {"_truncated": True, "_total_items": 120, "items": [{"id": 1}]},
                    },
                }

        agent.gateway = TruncatedGateway()
        tools = {
            "inspect_endpoint": _tool("inspect_endpoint", {"endpoint": {"type": "string"}}, ["endpoint"]),
            "execute_endpoint": _tool("execute_endpoint", {"endpoint": {"type": "string"}}, ["endpoint"]),
        }
        result = await agent._run_schema_inventory(
            {"resource": "organizations", "operation": "count"},
            tools,
            "qwen3-14b",
        )
        assert result["answer"] == "NinjaOne returned 120 organizations record(s)."

    import asyncio
    asyncio.run(scenario())
