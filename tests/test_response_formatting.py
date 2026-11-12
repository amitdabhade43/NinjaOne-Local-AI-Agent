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



def test_people_search_default_view_contains_only_name_email_and_category():
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
    compact = [{
        "firstName": records[0]["firstName"],
        "lastName": records[0]["lastName"],
        "email": records[0]["email"],
        "matched_category": records[0]["matched_category"],
    }]
    answer = NinjaOneAgent._format_records_fallback(compact, "Accounts matching 'Rene'")
    assert "Rene Piuk" in answer
    assert "rene@example.com" in answer
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
