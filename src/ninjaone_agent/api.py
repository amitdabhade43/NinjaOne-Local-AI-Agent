from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ninjaone_agent.agent import NinjaOneAgent
from ninjaone_agent.config import get_settings
from ninjaone_agent.foundry import FoundryClient, FoundryUnavailable
from ninjaone_agent.mcp_gateway import MCPGatewayError

app = FastAPI(title="NinjaOne Local AI Agent", version="0.2.0")
settings = get_settings()
agent = NinjaOneAgent(settings)
STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    model: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)
    session_id: str = Field(min_length=16, max_length=128)


class ApprovalRequest(BaseModel):
    approved: bool
    session_id: str = Field(min_length=16, max_length=128)


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ninjaone-local-ai-agent"}


@app.get("/api/models")
def models() -> dict[str, object]:
    try:
        foundry = FoundryClient(get_settings())
        found = foundry.list_models()
        return {"models": found, "count": len(found), "endpoint": foundry.endpoint}
    except FoundryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/tools")
async def tools() -> dict[str, object]:
    try:
        found = await agent.tools()
        return {"tools": found, "count": len(found)}
    except MCPGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, object]:
    try:
        return await agent.run(
            request.message, request.model, request.history, request.session_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (FoundryUnavailable, MCPGatewayError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent request failed: {exc}") from exc


@app.post("/api/approvals/{approval_id}")
async def resolve_approval(approval_id: str, request: ApprovalRequest) -> dict[str, object]:
    try:
        return await agent.approve(approval_id, request.approved, request.session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MCPGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
