import asyncio

from ninjaone_agent.agent import NinjaOneAgent


class FakeCatalog:
    def get(self, path, method="GET"):
        if path == "/v2/user/end-users" and method == "GET":
            return {
                "path": path,
                "method": method,
                "parameters": [
                    {"name": "page", "in": "query"},
                    {"name": "pageSize", "in": "query"},
                ],
                "response_fields": [
                    {"name": "items"},
                    {"name": "items.accessibleDeviceIds"},
                    {"name": "items.assignedDeviceIds"},
                ],
            }
        return None


class FakeGateway:
    def __init__(self, execute_result=None, paged_result=None):
        self.calls = []
        self.execute_result = execute_result or {
            "is_error": False,
            "structured_content": {
                "executed": True, "complete": True, "truncated": False,
                "data": {"items": [{
                    "id": 10, "firstName": "Richard", "lastName": "Otrebski",
                    "email": "richard@example.test",
                    "accessibleDeviceIds": [1, 2, 2, 3],
                    "assignedDeviceIds": [1, 2],
                }]},
            },
        }
        self.paged_result = paged_result

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "inspect_endpoint":
            return {"is_error": False, "structured_content": {
                "method": "GET", "path": "/v2/user/end-users",
                "parameters": [
                    {"name": "page", "in": "query"},
                    {"name": "pageSize", "in": "query"},
                ],
            }}
        if name == "execute_endpoint":
            return self.execute_result
        if name == "execute_paginated_endpoint":
            return self.paged_result
        raise AssertionError(name)


def _tool(name, properties, required=("endpoint",)):
    return {
        "name": name,
        "description": name,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
        },
    }


def _tools():
    endpoint = {"endpoint": {"type": "string"}}
    return {
        "inspect_endpoint": _tool("inspect_endpoint", endpoint),
        "execute_endpoint": _tool("execute_endpoint", {
            **endpoint, "path_params": {"type": "object"},
            "query": {"type": "object"}, "body": {},
        }),
        "execute_paginated_endpoint": _tool("execute_paginated_endpoint", {
            **endpoint, "path_params": {"type": "object"},
            "query": {"type": "object"}, "page_parameter": {"type": "string"},
            "page_size_parameter": {"type": "string"}, "page_size": {"type": "integer"},
            "start_page": {"type": "integer"}, "max_pages": {"type": "integer"},
            "result_path": {"type": "string"},
        }),
    }


def test_schema_person_device_access_counts_unique_ids():
    async def scenario():
        agent = object.__new__(NinjaOneAgent)
        agent.api_catalog = FakeCatalog()
        agent.gateway = FakeGateway()
        result = await agent._run_schema_person_device_access(
            "Richard Otrebski", _tools(), "qwen3-14b"
        )
        assert "can access **3 device(s)**" in result["answer"]
        assert "Assigned devices: **2**" in result["answer"]
        assert [call[0] for call in agent.gateway.calls] == [
            "inspect_endpoint", "execute_endpoint"
        ]
    asyncio.run(scenario())


def test_schema_collection_paginates_only_with_declared_parameters():
    async def scenario():
        paged = {
            "is_error": False,
            "structured_content": {
                "complete": True, "data": [
                    {"id": 10, "firstName": "Richard", "lastName": "Otrebski",
                     "email": "richard@example.test", "accessibleDeviceIds": [1, 2, 3]}
                ],
            },
        }
        gateway = FakeGateway(
            execute_result={"is_error": False, "structured_content": {
                "complete": False, "truncated": True, "data": {"items": []}
            }},
            paged_result=paged,
        )
        agent = object.__new__(NinjaOneAgent)
        agent.api_catalog = FakeCatalog()
        agent.gateway = gateway
        steps = []
        result, complete = await agent._run_schema_collection(
            "GET /v2/user/end-users", "end users", _tools(), steps
        )
        assert complete is True
        assert result == paged
        assert [call[0] for call in gateway.calls] == [
            "inspect_endpoint", "execute_endpoint", "execute_paginated_endpoint"
        ]
        page_args = gateway.calls[-1][1]
        assert page_args["page_parameter"] == "page"
        assert page_args["page_size_parameter"] == "pageSize"
        assert page_args["result_path"] == "items"
    asyncio.run(scenario())


def test_schema_people_search_uses_schema_driven_tools_and_deduplicates_email():
    async def scenario():
        agent = object.__new__(NinjaOneAgent)
        agent.api_catalog = FakeCatalog()
        agent.gateway = FakeGateway()
        # Only the end-user collection is present in this focused fixture.
        agent.api_catalog.get = lambda path, method="GET": (
            FakeCatalog().get(path, method)
            if path == "/v2/user/end-users" else None
        )
        result = await agent._run_schema_people_name_search(
            "Richard", _tools(), "qwen3-14b"
        )
        assert "**1 account(s)**" in result["answer"]
        assert "Richard Otrebski" in result["answer"]
        assert all(step["tool"] != "call" for step in result["steps"])
    asyncio.run(scenario())
