import re
import shutil
import subprocess
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openai import OpenAI

from ninjaone_agent.config import Settings


class FoundryUnavailable(RuntimeError):
    """Configured local Foundry endpoint cannot be reached."""


class FoundryClient:
    """OpenAI-compatible adapter with automatic Foundry Local port discovery."""

    # Foundry Local binds to loopback. Use character classes for dots so this
    # pattern stays readable without escape-sequence ambiguity.
    _LOCAL_URL = re.compile(
        r"https?://(?:127[.]0[.]0[.]1|localhost)(?::[0-9]+)?(?:/[^ ]*)?",
        re.IGNORECASE,
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self._base_url: str | None = None

    @property
    def endpoint(self) -> str | None:
        """Currently selected OpenAI-compatible endpoint, including /v1."""
        return self._base_url

    @classmethod
    def _extract_endpoint(cls, status_text: str) -> str | None:
        """Extract a loopback URL from Foundry CLI status output."""
        match = cls._LOCAL_URL.search(status_text or "")
        if not match:
            return None
        return cls._normalize_endpoint(match.group(0))

    @staticmethod
    def _normalize_endpoint(endpoint: str) -> str:
        """Ensure the endpoint points to the OpenAI-compatible /v1 API."""
        parsed = urlsplit(endpoint.strip().rstrip("/"))
        if not parsed.hostname or parsed.port == 0:
            raise ValueError("Foundry returned an invalid local endpoint.")
        path = parsed.path.rstrip("/")
        if not path.endswith("/v1"):
            path = f"{path}/v1" if path else "/v1"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def _discover_cli_endpoint(self) -> str:
        executable = shutil.which("foundry") or "foundry"
        errors: list[str] = []

        # Current CLI uses server; service is retained for older installations.
        for command in (
            [executable, "server", "status"],
            [executable, "service", "status"],
        ):
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(str(exc))
                continue

            output = (result.stdout or "") + "\n" + (result.stderr or "")
            if result.returncode == 0:
                endpoint = self._extract_endpoint(output)
                if endpoint:
                    return endpoint
                errors.append("Foundry status did not contain a loopback endpoint.")
            else:
                errors.append((result.stderr or result.stdout or "status command failed").strip())

        detail = "; ".join(item for item in errors if item)
        raise FoundryUnavailable(
            "Could not discover the Foundry Local endpoint. Ensure Foundry Local is running "
            "and the Foundry CLI is available. " + detail
        )

    def _candidate_base_urls(self, include_cached: bool = False) -> list[str]:
        candidates: list[str] = []

        def add(value: str | None) -> None:
            if value and value not in candidates:
                candidates.append(value)

        if include_cached:
            add(self._base_url)

        try:
            add(self._discover_cli_endpoint())
        except FoundryUnavailable:
            # Preserve compatibility with manually configured/custom endpoints.
            pass

        configured = (self.settings.foundry_base_url or "").strip()
        if configured:
            add(self._normalize_endpoint(configured))

        return candidates

    def _new_client(self, base_url: str) -> OpenAI:
        return OpenAI(
            base_url=base_url.rstrip("/") + "/",
            api_key=self.settings.foundry_api_key,
            timeout=float(self.settings.request_timeout_seconds),
        )

    def _execute(self, operation, *, include_cached: bool = False):
        candidates = self._candidate_base_urls(include_cached=include_cached)
        if not candidates:
            raise FoundryUnavailable(
                "No Foundry endpoint was found. Start Foundry Local and check its status."
            )

        errors: list[str] = []
        for base_url in candidates:
            client = self._new_client(base_url)
            try:
                result = operation(client)
                self._base_url = base_url
                return result
            except Exception as exc:
                errors.append(f"{base_url}: {exc}")

        raise FoundryUnavailable(
            "Could not connect to Foundry Local at the discovered/configured endpoint(s). "
            + " | ".join(errors)
        )

    def list_models(self) -> list[dict[str, str]]:
        try:
            response = self._execute(lambda client: client.models.list())
            return [
                {"id": item.id, "object": getattr(item, "object", "model")}
                for item in response.data
            ]
        except FoundryUnavailable:
            raise
        except Exception as exc:
            raise FoundryUnavailable(f"Could not list models from Foundry: {exc}") from exc

    def resolve_model(self, requested_model: str | None = None) -> str:
        available = [item["id"] for item in self.list_models()]
        selected = requested_model or self.settings.foundry_model
        if selected:
            if selected not in available:
                raise ValueError(
                    f"Model '{selected}' is not reported by Foundry. "
                    f"Available: {', '.join(available) or '(none)'}"
                )
            return selected
        if not available:
            raise FoundryUnavailable(
                "Foundry is reachable but returned no loaded models. Load a model first."
            )
        return available[0]

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Try native tools when supplied; caller owns safe fallback behavior."""
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            # Try the cached endpoint first; if it fails, discover the current port.
            return self._execute(
                lambda client: client.chat.completions.create(**kwargs),
                include_cached=True,
            )
        except FoundryUnavailable:
            raise
        except Exception as exc:
            raise FoundryUnavailable(f"Foundry chat request failed: {exc}") from exc

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
    ) -> dict[str, str]:
        selected = self.resolve_model(model)
        response = self.complete(model=selected, messages=messages)
        return {"model": selected, "answer": response.choices[0].message.content or ""}
