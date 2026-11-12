import json

from ninjaone_agent.api_catalog import NinjaOneAPICatalog, build_catalog


def sample_spec():
    return {
        "openapi": "3.0.0",
        "info": {"title": "NinjaOne Public API", "version": "2.0.9"},
        "paths": {
            "/v2/users": {
                "get": {
                    "operationId": "getUsers",
                    "summary": "List users",
                    "description": "Returns all console users.",
                    "tags": ["system", "users"],
                    "parameters": [{"name": "pageSize", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK", "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/UserList"}
                    }}}},
                }
            },
            "/v2/user/end-user/{id}/device-access": {
                "patch": {
                    "operationId": "updateEndUserDeviceAccess",
                    "summary": "Update device access",
                    "parameters": [{"name": "id", "in": "path", "required": True,
                                    "schema": {"type": "integer"}}],
                    "requestBody": {"content": {"application/json": {
                        "schema": {"type": "object", "properties": {
                            "deviceIds": {"type": "array", "description": "Device IDs",
                                          "items": {"type": "integer"}}
                        }}
                    }}},
                    "responses": {"200": {"description": "OK", "content": {"application/json": {
                        "schema": {"type": "object", "properties": {"success": {"type": "boolean"}}}
                    }}}},
                }
            },
        },
        "components": {"schemas": {"UserList": {
            "type": "object",
            "properties": {"users": {"type": "array", "items": {"$ref": "#/components/schemas/User"}}}
        }, "User": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "User ID"},
            "email": {"type": "string", "description": "Email address"},
            "invitationStatus": {"type": "string", "description": "Invitation status"},
            "passwordHash": {"type": "string", "description": "Must not be exposed"},
        }}}},
    }


def test_build_catalog_resolves_response_fields_and_parameters():
    catalog = build_catalog(sample_spec())
    assert catalog["endpoint_count"] == 2
    users = next(item for item in catalog["endpoints"] if item["path"] == "/v2/users")
    names = {field["name"] for field in users["response_fields"]}
    assert {"users", "users.id", "users.email", "users.invitationStatus"} <= names
    assert "users.passwordHash" in names
    assert users["parameters"][0]["name"] == "pageSize"


def test_catalog_search_finds_endpoint_by_response_field(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(build_catalog(sample_spec())), encoding="utf-8")
    catalog = NinjaOneAPICatalog(path)
    results = catalog.search("user invitation status", method="GET")
    assert results
    assert results[0]["path"] == "/v2/users"
    field_names = {field["name"] for field in results[0]["response_fields"]}
    assert "users.invitationStatus" in field_names
    assert "users.passwordHash" not in field_names


def test_missing_catalog_falls_back_to_mcp_discovery(tmp_path):
    catalog = NinjaOneAPICatalog(tmp_path / "missing.json")
    assert catalog.loaded is False
    assert catalog.search("users") == []
