from openai import OpenAI
from ninjaone_agent.config import Settings

class FoundryUnavailable(RuntimeError):
    """Configured local Foundry endpoint cannot be reached."""

class FoundryClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OpenAI(base_url=settings.foundry_base_url.rstrip("/") + "/", api_key=settings.foundry_api_key, timeout=90.0)

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
                raise ValueError(f"Model '{selected}' is not reported by Foundry. Available: {', '.join(available) or '(none)'}")
            return selected
        if not available:
            raise FoundryUnavailable("Foundry returned no models. Load a model first.")
        return available[0]

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> dict[str, str]:
        selected = self.resolve_model(model)
        try:
            response = self.client.chat.completions.create(model=selected, messages=messages)
            return {"model": selected, "answer": response.choices[0].message.content or ""}
        except Exception as exc:
            raise FoundryUnavailable(f"Foundry chat request failed: {exc}") from exc
