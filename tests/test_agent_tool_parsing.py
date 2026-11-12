from ninjaone_agent.agent import (
    _clean_model_content,
    _tool_calls_from_content,
    _validate_tool_arguments,
)


def test_parses_qwen_xml_tool_call():
    content = """<think>internal reasoning</think>
<tool_call>
{"name": "find_endpoint", "arguments": {"query": "organizations", "limit": 10}}
</tool_call>"""
    assert _tool_calls_from_content(content) == [
        ("find_endpoint", {"query": "organizations", "limit": 10})
    ]


def test_parses_multiple_qwen_tool_calls():
    content = (
        '<tool_call>{"name":"find_endpoint","arguments":{"query":"orgs"}}</tool_call>'
        '<tool_call>{"name":"describe_endpoint","arguments":{"endpoint":"GET /v2/organizations"}}</tool_call>'
    )
    assert _tool_calls_from_content(content) == [
        ("find_endpoint", {"query": "orgs"}),
        ("describe_endpoint", {"endpoint": "GET /v2/organizations"}),
    ]


def test_parses_legacy_json_tool_plan():
    assert _tool_calls_from_content(
        '{"tool_name":"find_endpoint","arguments":{"query":"organizations"}}'
    ) == [("find_endpoint", {"query": "organizations"})]


def test_does_not_parse_malformed_or_non_object_tool_arguments():
    assert _tool_calls_from_content(
        '<tool_call>{"name":"find_endpoint","arguments":[]}</tool_call>'
    ) == []
    assert _tool_calls_from_content("<tool_call>{broken}</tool_call>") == []


def test_removes_think_and_tool_call_markup_from_user_output():
    assert _clean_model_content(
        "<think>private reasoning</think>Here is the result."
        '<tool_call>{"name":"call","arguments":{}}</tool_call>'
    ) == "Here is the result."


def test_validates_required_arguments_and_types():
    schema = {
        "type": "object",
        "required": ["endpoint"],
        "properties": {
            "endpoint": {"type": "string"},
            "query": {"type": "object"},
        },
    }
    assert _validate_tool_arguments(schema, {"endpoint": "GET /v2/organizations"}) is None
    assert "Missing required" in _validate_tool_arguments(schema, {})
    assert "must be of type string" in _validate_tool_arguments(schema, {"endpoint": 123})
    assert "must be of type object" in _validate_tool_arguments(
        schema, {"endpoint": "GET /v2/organizations", "query": "wrong"}
    )


def test_rejects_unknown_arguments_when_schema_disallows_them():
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "additionalProperties": False,
    }
    assert "Unknown argument" in _validate_tool_arguments(schema, {"query": "orgs", "unexpected": True})
