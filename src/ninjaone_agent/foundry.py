from typing import Any
from openai import OpenAI
from ninjaone_agent.config import Settings


class FoundryUnavailable(RuntimeError):
    """Configured local Foundry endpoint cannot be reached."""


class FoundryClient:
    """Small OpenAI-compatible adapter; no model ID is hard-coded."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OpenAI(
            base_url=settings.foundry_base_url.rstrip("/") + "/",
            api_key=settings.foundry_api_key,
            timeout=float(settings.request_timeout_seconds),
        )

    def list_models(self) -> list[dict[str, str]]:
        try:
            response = self.client.models.list()
            return [{"id": item.id, "object": getattr(item, "object", "model")} for item in response.data]
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
            raise FoundryUnavailable("Foundry returned no models. Load a model first.")
        return available[0]

    def complete(self, *, model: str, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]] | None = None) -> Any:
        """Try native tools when supplied; caller owns safe fallback behavior."""
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise FoundryUnavailable(f"Foundry chat request failed: {exc}") from exc

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> dict[str, str]:
        selected = self.resolve_model(model)
        response = self.complete(model=selected, messages=messages)
        return {"model": selected, "answer": response.choices[0].message.content or ""}
