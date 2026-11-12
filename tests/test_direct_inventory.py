from ninjaone_agent.agent import (
    _direct_inventory_intent,
    _endpoint_candidate,
    _inventory_records,
)


def test_direct_inventory_intent_routes_simple_inventory_questions():
    assert _direct_inventory_intent("List all organizations") == {
        "resource": "organizations", "operation": "list"
    }
    assert _direct_inventory_intent("How many devices do we manage?") == {
        "resource": "devices", "operation": "count"
    }


def test_direct_inventory_intent_skips_filtered_requests():
    assert _direct_inventory_intent("Show devices where status is offline") is None
    assert _direct_inventory_intent("Investigate device 12345") is None


def test_endpoint_candidate_requires_documented_get_path():
    assert _endpoint_candidate({
        "structured_content": {"results": [
            {"method": "GET", "path": "/v2/organizations"}
        ]}
    }) == {"method": "GET", "path": "/v2/organizations"}
    assert _endpoint_candidate({
        "structured_content": {"results": [
            {"method": "POST", "path": "/v2/organizations"}
        ]}
    }) is None


def test_inventory_records_extracts_common_collection_envelope():
    result = {"content": ['{"data":[{"id":1,"name":"Example"}]}']}
    assert _inventory_records(result) == [{"id": 1, "name": "Example"}]
