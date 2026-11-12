from types import SimpleNamespace

from ninjaone_agent.agent import NinjaOneAgent


class FakeFoundry:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def test_fallback_formats_people_records_as_readable_markdown():
    records = [{
        "id": 2,
        "firstName": "Rene",
        "lastName": "Piuk",
        "email": "rene@example.com",
        "enabled": True,
        "administrator": False,
        "matched_category": "console users",
    }]

    answer = NinjaOneAgent._format_records_fallback(records, "People matching Rene")

    assert "### People matching Rene" in answer
    assert "**1. Rene Piuk**" in answer
    assert "**Email:** rene@example.com" in answer
    assert "**Enabled:** Yes" in answer
    assert "matched_category" not in answer


def test_structured_result_uses_local_llm_without_tools_and_returns_markdown():
    agent = NinjaOneAgent.__new__(NinjaOneAgent)
    agent.foundry = FakeFoundry("### People matching Rene\n\n**1. Rene Piuk**")
    answer = agent._format_structured_result(
        "local-model", "Find Rene", [{"id": 2, "firstName": "Rene"}], "fallback"
    )

    assert answer.startswith("### People")
    assert len(agent.foundry.calls) == 1
    assert "tools" not in agent.foundry.calls[0]
    assert agent.foundry.calls[0]["messages"][0]["role"] == "system"


def test_structured_result_uses_fallback_when_llm_returns_json():
    agent = NinjaOneAgent.__new__(NinjaOneAgent)
    agent.foundry = FakeFoundry('{"id": 2}')
    answer = agent._format_structured_result(
        "local-model", "Find Rene", [{"id": 2}], "formatted fallback"
    )
    assert answer == "formatted fallback"


def test_structured_result_uses_fallback_for_large_result_sets():
    agent = NinjaOneAgent.__new__(NinjaOneAgent)
    agent.foundry = FakeFoundry("should not be called")
    records = [{"id": number} for number in range(31)]

    answer = agent._format_structured_result(
        "local-model", "List records", records, "large result fallback"
    )

    assert answer == "large result fallback"
    assert agent.foundry.calls == []



def test_people_directory_shows_only_account_name_and_email():
    from ninjaone_agent.agent import _format_people_directory

    records = [{
        "id": 2,
        "firstName": "Rene",
        "lastName": "Piuk",
        "email": "rene@example.com",
        "enabled": True,
        "administrator": True,
        "deviceIds": [50, 53],
        "matched_category": "console users",
    }]
    answer = _format_people_directory(records, "Rene")
    assert "**1 account(s)**" in answer
    assert "- **Rene Piuk** — rene@example.com" in answer
    assert "Enabled" not in answer
    assert "Administrator" not in answer
    assert "deviceIds" not in answer


def test_people_name_search_strips_ninjaone_scope_suffix():
    from ninjaone_agent.agent import _people_name_search_intent

    assert _people_name_search_intent(
        "Find all users with name Rene in ninjaone"
    ) == "Rene"
    assert _people_name_search_intent(
        "Find users named Rene on NinjaOne"
    ) == "Rene"
    assert _people_name_search_intent(
        "Find all users with name Rene Piuk"
    ) == "Rene Piuk"


def test_device_access_intent_extracts_named_person():
    from ninjaone_agent.agent import _device_access_name_intent

    assert _device_access_name_intent(
        "how many device access has Richard Otrebski in ninjaone"
    ) == "Richard Otrebski"
    assert _device_access_name_intent(
        "How many devices can Richard Otrebski access?"
    ) == "Richard Otrebski"
    assert _device_access_name_intent("How many devices are in NinjaOne?") is None


def test_device_access_fast_path_uses_catalogue_and_counts_accessible_ids():
    import asyncio

    class FakeCatalog:
        def get(self, path, method="GET"):
            assert path == "/v2/user/end-users"
            assert method == "GET"
            return {"path": path, "response_fields": [
                {"name": "accessibleDeviceIds", "type": "array"}
            ]}

    class FakeGateway:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"structured_content": [
                {
                    "id": 17, "firstName": "Richard", "lastName": "Otrebski",
                    "email": "richard@example.com",
                    "accessibleDeviceIds": [101, 102, 103],
                    "assignedDeviceIds": [101, 102],
                }
            ]}

    agent = NinjaOneAgent.__new__(NinjaOneAgent)
    agent.api_catalog = FakeCatalog()
    agent.gateway = FakeGateway()
    tool = {
        "name": "call",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string"},
                "path_params": {"type": "object"},
                "query": {"type": "object"},
            },
            "required": ["endpoint"],
        },
    }

    result = asyncio.run(agent._run_person_device_access(
        "Richard Otrebski", {"call": tool}, "test-model"
    ))

    assert "can access **3 device(s)**" in result["answer"]
    assert "Assigned devices: **2**" in result["answer"]
    assert len(agent.gateway.calls) == 1
    assert agent.gateway.calls[0][0] == "call"
