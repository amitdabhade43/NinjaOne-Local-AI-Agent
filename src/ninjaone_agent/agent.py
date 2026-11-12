import json
import logging
import uuid
from typing import Any

from ninjaone_agent.config import Settings
from ninjaone_agent.foundry import FoundryClient, FoundryUnavailable
from ninjaone_agent.mcp_gateway import NinjaOneMCPGateway
from ninjaone_agent.safety import approval_decision, classify_tool

logger = logging.getLogger(__name__)


def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        result.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": (tool.get("description") or tool["name"])[:4000],
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return result


def _json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from plain model output, including fenced output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json", "", 1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


class NinjaOneAgent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.foundry = FoundryClient(settings)
        self.gateway = NinjaOneMCPGateway(settings.ninjaone_mcp_url)
        self.pending: dict[str, dict[str, Any]] = {}

    def models(self) -> list[dict[str, str]]:
        return self.foundry.list_models()

    async def tools(self) -> list[dict[str, Any]]:
        return await self.gateway.list_tools()

    async def approve(self, approval_id: str, approved: bool) -> dict[str, Any]:
        pending = self.pending.pop(approval_id, None)
        if not pending:
            raise KeyError("Approval request was not found or has expired.")
        if not approved:
            return {"status": "rejected", "message": "The operation was not executed."}
        tool = pending["tool"]
        kind = classify_tool(tool)
        if kind == "destructive" and not self.settings.allow_delete_operations:
            return {"status": "blocked", "message": "Destructive operations are disabled by configuration."}
        result = await self.gateway.call_tool(pending["tool_name"], pending["arguments"])
        return {"status": "executed", "tool": pending["tool_name"], "result": result}

    async def run(self, message: str, model: str | None = None,
                  history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        selected = self.foundry.resolve_model(model)
        available = await self.gateway.list_tools()
        by_name = {item["name"]: item for item in available}
        if not available:
            raise RuntimeError("The NinjaOne MCP gateway returned no tools.")

        messages: list[dict[str, Any]] = [{
            "role": "system",
            "content": (
                "You are a careful NinjaOne operations assistant. Use MCP tools for live NinjaOne facts. "
                "Never invent organizations, users, devices, IDs, or tool results. Inspect tool descriptions and "
                "schemas. Prefer read-only tools. Do not claim an operation succeeded unless its tool result confirms it. "
                "If no tool is needed, answer normally. Keep answers concise and identify missing data honestly."
            ),
        }]
        for item in (history or [])[-8:]:
            if item.get("role") in ("user", "assistant") and item.get("content"):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": message})

        tool_specs = _openai_tools(available)
        transcript: list[dict[str, Any]] = []
        for step in range(self.settings.max_agent_steps):
            try:
                response = self.foundry.complete(model=selected, messages=messages, tools=tool_specs)
            except FoundryUnavailable:
                # Fallback for runtimes/models that reject native function-calling parameters.
                response = self.foundry.complete(
                    model=selected,
                    messages=messages + [{
                        "role": "system",
                        "content": (
                            "Native tool calling is unavailable. If a tool is needed, return ONLY JSON in this format: "
                            '{"tool_name":"exact_name","arguments":{}}. Otherwise answer normally. '
                            "Available tools: " + json.dumps(
                                [{"name": t["name"], "description": t["description"],
                                  "input_schema": t["input_schema"]} for t in available],
                                ensure_ascii=False
                            )
                        ),
                    }],
                )

            assistant = response.choices[0].message
            native_calls = getattr(assistant, "tool_calls", None) or []
            if native_calls:
                messages.append(assistant.model_dump(exclude_none=True))
                calls = [(call.function.name, json.loads(call.function.arguments or "{}")) for call in native_calls]
            else:
                content = assistant.content or ""
                parsed = _json_object(content)
                if parsed and isinstance(parsed.get("tool_name"), str):
                    calls = [(parsed["tool_name"], parsed.get("arguments") or {})]
                else:
                    return {"model": selected, "answer": content, "steps": transcript,
                            "pending_approvals": []}

            for tool_name, arguments in calls:
                tool = by_name.get(tool_name)
                if not tool:
                    transcript.append({"tool": tool_name, "status": "rejected", "error": "Unknown MCP tool name."})
                    messages.append({"role": "system", "content": f"Tool {tool_name} does not exist. Choose from: {', '.join(by_name)}"})
                    continue
                if not isinstance(arguments, dict):
                    arguments = {}
                allowed, decision = approval_decision(tool, self.settings.allow_delete_operations)
                if not allowed:
                    if decision == "blocked":
                        transcript.append({"tool": tool_name, "status": "blocked",
                                           "message": "Destructive operation is disabled by configuration."})
                        messages.append({"role": "system", "content": f"{tool_name} is blocked by the safety policy. Do not retry it."})
                        continue
                    approval_id = str(uuid.uuid4())
                    self.pending[approval_id] = {"tool": tool, "tool_name": tool_name, "arguments": arguments}
                    transcript.append({"tool": tool_name, "status": "approval_required", "approval_id": approval_id})
                    return {
                        "model": selected,
                        "answer": "This request requires approval before the operation can run.",
                        "steps": transcript,
                        "pending_approvals": [{"approval_id": approval_id, "tool": tool_name,
                                               "arguments": arguments, "risk": classify_tool(tool)}],
                    }
                try:
                    result = await self.gateway.call_tool(tool_name, arguments)
                    transcript.append({"tool": tool_name, "status": "error" if result["is_error"] else "completed",
                                       "result": result})
                    messages.append({"role": "tool", "tool_call_id": getattr(native_calls[0], "id", "fallback"),
                                     "content": json.dumps(result, ensure_ascii=False, default=str)})
                except Exception as exc:
                    logger.exception("MCP tool execution failed: %s", tool_name)
                    transcript.append({"tool": tool_name, "status": "error", "error": str(exc)})
                    messages.append({"role": "system", "content": f"Tool {tool_name} failed: {exc}. Explain the failure; do not fabricate results."})

        final = self.foundry.complete(
            model=selected,
            messages=messages + [{"role": "system", "content": "Summarize only the tool results already present. If incomplete, say so."}],
        )
        return {"model": selected, "answer": final.choices[0].message.content or "",
                "steps": transcript, "pending_approvals": []}
