from ninjaone_agent.agent import _tool_failed


def test_gateway_error_payload_is_not_reported_as_success():
    assert _tool_failed({
        "is_error": False,
        "structured_content": {"error": "Missing required query parameter", "executed": False},
    })


def test_successful_gateway_execution_is_not_reported_as_failure():
    assert not _tool_failed({
        "is_error": False,
        "structured_content": {"executed": True, "complete": True, "data": []},
    })


def test_mcp_protocol_error_is_reported_as_failure():
    assert _tool_failed({"is_error": True, "content": ["tool failed"]})
