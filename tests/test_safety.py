from ninjaone_agent.safety import approval_decision, classify_tool


def tool(name, read_only=None, destructive=None, description=""):
    return {"name": name, "description": description,
            "read_only_hint": read_only, "destructive_hint": destructive}


def test_explicit_read_only_is_allowed():
    assert classify_tool(tool("unknown", read_only=True)) == "read"
    assert approval_decision(tool("unknown", read_only=True), False)[0] is True


def test_unknown_tool_requires_approval():
    assert classify_tool(tool("mystery_operation")) == "write"
    assert approval_decision(tool("mystery_operation"), False)[1] == "approval_required"


def test_delete_is_blocked_by_default():
    item = tool("delete_device")
    assert classify_tool(item) == "destructive"
    assert approval_decision(item, False)[1] == "blocked"


def test_list_get_are_read_only_fallback():
    assert classify_tool(tool("list_devices")) == "read"
    assert classify_tool(tool("get_device")) == "read"


def test_dynamic_gateway_call_uses_http_method():
    generic = tool("call")
    assert classify_tool(generic, {"method": "GET"}) == "read"
    assert approval_decision(generic, False, {"method": "POST"})[1] == "approval_required"
    assert approval_decision(generic, False, {"method": "DELETE"})[1] == "blocked"



def test_put_substring_in_input_or_output_does_not_mark_read_tool_as_write():
    assert classify_tool(tool("get_device", description="Returns input and output details")) == "read"


def test_remove_in_read_tool_description_does_not_mark_it_destructive():
    item = tool("list_devices", description="Lists devices and explains how to remove one")
    assert classify_tool(item) == "read"


def test_exact_destructive_action_in_tool_name_is_blocked():
    item = tool("remove_device")
    assert classify_tool(item) == "destructive"
    assert approval_decision(item, False)[1] == "blocked"


def test_http_method_takes_precedence_over_tool_name():
    assert classify_tool(tool("remove_device"), {"http_method": "GET"}) == "read"
