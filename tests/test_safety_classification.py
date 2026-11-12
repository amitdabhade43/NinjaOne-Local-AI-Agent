from ninjaone_agent.safety import classify_tool, approval_decision


def test_endpoint_inspection_is_read_only():
    assert classify_tool({"name": "inspect_endpoint", "description": "Inspect endpoint"}) == "read"


def test_paginated_endpoint_is_read_only_when_gateway_marks_get():
    tool = {"name": "execute_paginated_endpoint", "description": "GET pagination"}
    assert classify_tool(tool, {"http_method": "GET"}) == "read"


def test_dynamic_endpoint_method_controls_risk():
    tool = {"name": "execute_endpoint", "description": "Execute an OpenAPI endpoint"}
    assert classify_tool(tool, {"http_method": "GET"}) == "read"
    assert classify_tool(tool, {"http_method": "POST"}) == "write"
    assert classify_tool(tool, {"http_method": "PATCH"}) == "write"
    assert classify_tool(tool, {"http_method": "DELETE"}) == "destructive"


def test_raw_request_method_controls_risk():
    tool = {"name": "execute_raw_request", "description": "Execute a concrete API request"}
    assert classify_tool(tool, {"http_method": "GET"}) == "read"
    assert classify_tool(tool, {"http_method": "PUT"}) == "write"


def test_unknown_tools_default_to_write():
    assert classify_tool({"name": "unknown_action", "description": "Unknown"}) == "write"


def test_write_requires_approval_and_delete_is_blocked():
    tool = {"name": "execute_endpoint", "description": "Execute endpoint"}
    assert approval_decision(tool, False, {"http_method": "PATCH"}) == (False, "approval_required")
    assert approval_decision(tool, False, {"http_method": "DELETE"}) == (False, "blocked")
