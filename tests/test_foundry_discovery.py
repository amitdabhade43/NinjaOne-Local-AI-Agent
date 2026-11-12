from ninjaone_agent.config import Settings
from ninjaone_agent.foundry import FoundryClient


def test_extracts_dynamic_loopback_endpoint_from_cli_status():
    status = "Service Status: Running\nLocal Endpoint: http://127.0.0.1:53821"
    assert FoundryClient._extract_endpoint(status) == "http://127.0.0.1:53821/v1"


def test_extracts_endpoint_when_v1_is_already_present():
    status = "Local Endpoint: http://localhost:53821/v1"
    assert FoundryClient._extract_endpoint(status) == "http://localhost:53821/v1"


def test_ignores_non_loopback_urls():
    status = "Docs: https://learn.microsoft.com and endpoint: http://192.168.1.10:53821"
    assert FoundryClient._extract_endpoint(status) is None


def test_normalizes_endpoint_without_duplicating_v1():
    assert FoundryClient._normalize_endpoint("http://127.0.0.1:53821/v1/") == (
        "http://127.0.0.1:53821/v1"
    )


def test_dynamic_cli_endpoint_precedes_configured_fallback(monkeypatch):
    settings = Settings(foundry_base_url="http://127.0.0.1:5273/v1")
    client = FoundryClient(settings)
    monkeypatch.setattr(
        client,
        "_discover_cli_endpoint",
        lambda: "http://127.0.0.1:53821/v1",
    )

    assert client._candidate_base_urls() == [
        "http://127.0.0.1:53821/v1",
        "http://127.0.0.1:5273/v1",
    ]


def test_configured_endpoint_is_fallback_when_cli_discovery_fails(monkeypatch):
    settings = Settings(foundry_base_url="http://127.0.0.1:5273/v1")
    client = FoundryClient(settings)

    def unavailable():
        from ninjaone_agent.foundry import FoundryUnavailable
        raise FoundryUnavailable("CLI not available")

    monkeypatch.setattr(client, "_discover_cli_endpoint", unavailable)

    assert client._candidate_base_urls() == ["http://127.0.0.1:5273/v1"]
