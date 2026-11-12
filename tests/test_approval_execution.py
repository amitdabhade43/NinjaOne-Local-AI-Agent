import asyncio
from pathlib import Path

from ninjaone_agent.agent import NinjaOneAgent


class FakeGateway:
    def __init__(self):
        self.env_file = Path("gateway.env")
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"is_error": False, "structured_content": {"executed": True}}


def _agent_with_pending(context):
    agent = object.__new__(NinjaOneAgent)
    agent.pending = {
        "approval-id": {
            "kind": "write",
            "tool_name": "call",
            "arguments": {"endpoint": "PATCH /v2/organizations/6", "body": {"name": "New"}},
            "approval_context": context,
        }
    }
    agent.gateway = FakeGateway()
    return agent


def test_legacy_gateway_approval_executes_without_token_when_schema_has_none():
    agent = _agent_with_pending({"inject_token": False})
    result = asyncio.run(agent.approve("approval-id", True))
    assert result["status"] == "executed"
    assert agent.gateway.calls[0][0] == "call"
    assert "approval_token" not in agent.gateway.calls[0][1]
    assert "approval-id" not in agent.pending


def test_schema_gateway_token_is_added_only_after_approval(monkeypatch):
    import ninjaone_agent.agent as agent_module

    monkeypatch.setattr(agent_module, "mint_approval_token", lambda **kwargs: "backend-token")
    agent = _agent_with_pending({
        "inject_token": True,
        "method": "PATCH",
        "path": "/v2/organizations/6",
        "body": {"name": "New"},
    })
    result = asyncio.run(agent.approve("approval-id", True))
    assert result["status"] == "executed"
    assert agent.gateway.calls[0][1]["approval_token"] == "backend-token"


def test_rejected_approval_never_calls_gateway():
    agent = _agent_with_pending({"inject_token": True, "method": "PATCH", "path": "/v2/x", "body": {}})
    result = asyncio.run(agent.approve("approval-id", False))
    assert result["status"] == "rejected"
    assert agent.gateway.calls == []
