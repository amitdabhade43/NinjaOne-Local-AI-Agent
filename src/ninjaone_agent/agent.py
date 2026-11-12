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
    return [{
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": (tool.get("description") or tool["name"])[:4000],
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        },
    } for tool in tools]


def _json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from plain or fenced model output."""
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
        self.gateway = NinjaOneMCPGateway(settings)
        # Pending approvals are intentionally in-memory and expire on process restart.
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
        kind = classify_tool(pending["tool"], pending["arguments"])
        if kind == "destructive" and not self.settings.allow_delete_operations:
            return {"status": "blocked", "message": "Destructive operations are disabled by configuration."}
        result = await self.gateway.call_tool(pending["tool_name"], pending["arguments"])
        return {"status": "executed" if not result["is_error"] else "error",
                "tool": pending["tool_name"], "result": result}

    async def run(self, message: str, model: str | None = None,
                  history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        selected = self.foundry.resolve_model(model)
        try:
            available = await self.gateway.list_tools()
        except Exception as exc:
            # General conversation remains available when the NinjaOne gateway is offline.
            logger.warning("NinjaOne MCP tools unavailable for this request: %s", exc)
            available = []
        by_name = {item["name"]: item for item in available}

        system_text = (
            "You are a careful NinjaOne operations assistant. Use MCP tools for live NinjaOne facts when available. "
            "Never invent organizations, users, devices, IDs, or tool results. Inspect tool descriptions and schemas. "
            "Prefer read-only tools. Do not claim an operation succeeded unless its tool result confirms it. "
            "If no tool is needed, answer normally. Keep answers concise and identify missing data honestly."
        )
        if not available:
            system_text += (
                " The NinjaOne MCP gateway is currently unavailable or returned no tools. "
                "You may answer general questions, but do not claim to have retrieved live tenant data."
            )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
        for item in (history or [])[-8:]:
            if item.get("role") in ("user", "assistant") and item.get("content"):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": message})

        tool_specs = _openai_tools(available)
        transcript: list[dict[str, Any]] = []
        for _step in range(self.settings.max_agent_steps):
            used_fallback = False
            try:
                response = self.foundry.complete(
                    model=selected, messages=messages, tools=tool_specs if tool_specs else None
                )
            except FoundryUnavailable:
                used_fallback = True
                tool_reference = json.dumps(
                    [{"name": t["name"], "description": t["description"],
                      "input_schema": t["input_schema"]} for t in available],
                    ensure_ascii=False,
                )
                response = self.foundry.complete(
                    model=selected,
                    messages=messages + [{
                        "role": "system",
                        "content": (
                            "The model endpoint does not support native tool calling. If a tool is needed, return "
                            "ONLY a JSON object like "
                            '{"tool_name":"exact_name","arguments":{}}. If no tool is needed, answer in plain text. '
                            "Available tools and schemas: " + tool_reference
                        ),
                    }],
                )

            assistant = response.choices[0].message
            native_calls = getattr(assistant, "tool_calls", None) or []
            calls: list[tuple[str, dict[str, Any], str | None]] = []
            if native_calls:
                messages.append(assistant.model_dump(exclude_none=True))
                for call in native_calls:
                    try:
                        args = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    calls.append((call.function.name, args if isinstance(args, dict) else {}, call.id))
            else:
                content = assistant.content or ""
                parsed = _json_object(content) if used_fallback and available else None

                # Some models accept the tools parameter but do not emit tool_calls.
                # Give those models one explicit JSON-planning fallback before trusting
                # a plain-text answer to a question that may require live tenant data.
                if not parsed and not used_fallback and available:
                    tool_reference = json.dumps(
                        [{"name": t["name"], "description": t["description"],
                          "input_schema": t["input_schema"]} for t in available],
                        ensure_ascii=False,
                    )
                    try:
                        fallback = self.foundry.complete(
                            model=selected,
                            messages=messages + [{
                                "role": "system",
                                "content": (
                                    "Decide whether this user request needs live NinjaOne data or an operation. "
                                    "If yes, return ONLY JSON: "
                                    '{"tool_name":"exact_name","arguments":{}}. '
                                    "If no tool is needed, answer in plain text. Available tools: " + tool_reference
                                ),
                            }],
                        )
                        fallback_content = fallback.choices[0].message.content or ""
                        parsed = _json_object(fallback_content)
                        if parsed:
                            content = fallback_content
                    except FoundryUnavailable:
                        logger.warning("JSON planning fallback is unavailable for model %s", selected)

                if parsed and isinstance(parsed.get("tool_name"), str):
                    messages.append({"role": "assistant", "content": content})
                    args = parsed.get("arguments") or {}
                    calls.append((parsed["tool_name"], args if isinstance(args, dict) else {}, None))
                else:
                    return {"model": selected, "answer": content, "steps": transcript,
                            "pending_approvals": []}

            for tool_name, arguments, call_id in calls:
                tool = by_name.get(tool_name)
                if not tool:
                    transcript.append({"tool": tool_name, "status": "rejected", "error": "Unknown MCP tool name."})
                    messages.append({"role": "system", "content": f"Tool {tool_name} does not exist. Available tools: {', '.join(by_name)}"})
                    continue

                kind = classify_tool(tool, arguments)
                allowed, decision = approval_decision(tool, self.settings.allow_delete_operations, arguments)
                if kind == "write" and not self.settings.approval_required_for_writes:
                    allowed, decision = True, "allowed"
                if not allowed:
                    if decision == "blocked":
                        transcript.append({"tool": tool_name, "status": "blocked",
                                           "message": "Destructive operation is disabled by configuration."})
                        messages.append({"role": "system", "content": f"{tool_name} is blocked by policy. Do not retry it."})
                        continue
                    approval_id = str(uuid.uuid4())
                    self.pending[approval_id] = {"tool": tool, "tool_name": tool_name, "arguments": arguments}
                    transcript.append({"tool": tool_name, "status": "approval_required", "approval_id": approval_id})
                    return {
                        "model": selected,
                        "answer": "This request requires approval before the operation can run.",
                        "steps": transcript,
                        "pending_approvals": [{"approval_id": approval_id, "tool": tool_name,
                                               "arguments": arguments, "risk": kind}],
                    }

                try:
                    result = await self.gateway.call_tool(tool_name, arguments)
                    transcript.append({"tool": tool_name, "status": "error" if result["is_error"] else "completed",
                                       "result": result})
                    tool_text = json.dumps(result, ensure_ascii=False, default=str)
                    if call_id:
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": tool_text})
                    else:
                        messages.append({"role": "user", "content": f"Tool result for {tool_name}: {tool_text}"})
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
