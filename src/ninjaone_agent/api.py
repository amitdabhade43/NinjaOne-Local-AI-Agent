from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from ninjaone_agent.config import get_settings
from ninjaone_agent.foundry import FoundryClient, FoundryUnavailable

app = FastAPI(title="NinjaOne Local AI Agent", version="0.1.0")

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    model: str | None = None

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ninjaone-local-ai-agent"}

@app.get("/api/models")
def models() -> dict[str, object]:
    try:
        found = FoundryClient(get_settings()).list_models()
        return {"models": found, "count": len(found)}
    except FoundryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    try:
        return FoundryClient(get_settings()).chat([{"role": "user", "content": request.message}], request.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FoundryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
