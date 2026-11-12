import base64
import hashlib
import hmac
import json

import pytest

from ninjaone_agent.approval import (
    ApprovalSigningError,
    canonical_body_digest,
    mint_approval_token,
    resolve_path,
)


def _decode_token(token):
    encoded, signature = token.split(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    return encoded, payload, supplied


def test_mints_gateway_compatible_signed_token(monkeypatch):
    secret = "test-secret-not-for-production"
    monkeypatch.setenv("NINJA_MCP_APPROVAL_SECRET", secret)
    body = {"name": "Test", "enabled": True}
    token = mint_approval_token(method="PATCH", path="/v2/organizations/42", body=body)

    encoded, payload, supplied = _decode_token(token)
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()

    assert hmac.compare_digest(expected, supplied)
    assert payload["method"] == "PATCH"
    assert payload["path"] == "/v2/organizations/42"
    assert payload["body_sha256"] == canonical_body_digest(body)
    assert payload["jti"]
    assert payload["exp"] > 0


def test_resolves_path_parameters_like_gateway():
    assert resolve_path("/v2/devices/{deviceId}/custom-fields/{field}", {
        "deviceId": 123, "field": "asset tag"
    }) == "/v2/devices/123/custom-fields/asset%20tag"


@pytest.mark.parametrize("params", [
    {},
    {"deviceId": 1, "unexpected": "value"},
    {"deviceId": {"nested": "object"}},
])
def test_rejects_unsafe_path_parameters(params):
    with pytest.raises(ApprovalSigningError):
        resolve_path("/v2/devices/{deviceId}", params)


def test_refuses_to_mint_without_secret(monkeypatch):
    monkeypatch.delenv("NINJA_MCP_APPROVAL_SECRET", raising=False)
    with pytest.raises(ApprovalSigningError):
        mint_approval_token(method="POST", path="/v2/tickets", body={})


def test_refuses_get_and_unresolved_paths(monkeypatch):
    monkeypatch.setenv("NINJA_MCP_APPROVAL_SECRET", "test-secret")
    with pytest.raises(ApprovalSigningError):
        mint_approval_token(method="GET", path="/v2/devices", body=None)
    with pytest.raises(ApprovalSigningError):
        mint_approval_token(method="POST", path="/v2/devices/{id}", body={})
