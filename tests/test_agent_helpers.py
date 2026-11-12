from ninjaone_agent.agent import _json_object, _openai_tools


def test_json_object_extracts_fenced_json():
    assert _json_object('```json\n{"tool_name":"list_devices","arguments":{}}\n```') == {
        "tool_name": "list_devices", "arguments": {}
    }


def test_json_object_rejects_plain_text():
    assert _json_object("Here is the answer.") is None


def test_mcp_tool_maps_to_openai_schema():
    mapped = _openai_tools([{"name": "list_devices", "description": "List devices",
                             "input_schema": {"type": "object", "properties": {}}}])
    assert mapped[0]["function"]["name"] == "list_devices"
