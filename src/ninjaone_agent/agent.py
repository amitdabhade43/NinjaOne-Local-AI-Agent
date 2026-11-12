import json
from contextlib import AsyncExitStack
import logging
import re
import uuid
from typing import Any

from ninjaone_agent.config import Settings
from ninjaone_agent.foundry import FoundryClient, FoundryUnavailable
from ninjaone_agent.mcp_gateway import MCPGatewayError, NinjaOneMCPGateway
from ninjaone_agent.safety import approval_decision, classify_tool
from ninjaone_agent.skills import render_master_instructions, render_skill_context
from ninjaone_agent.approval import (
    ApprovalSigningError,
    mint_approval_token,
    resolve_path,
)
from urllib.parse import urlsplit

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


def _tool_calls_from_content(content: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse Qwen <tool_call> markup and the legacy JSON tool-plan format."""
    calls: list[tuple[str, dict[str, Any]]] = []

    blocks = re.findall(
        r"<tool_call>\s*(.*?)\s*</tool_call>",
        content or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        name = payload.get("name") or payload.get("tool_name")
        arguments = payload.get("arguments", {})
        if isinstance(name, str) and name and isinstance(arguments, dict):
            calls.append((name, arguments))

    if calls:
        return calls

    payload = _json_object(content or "")
    if payload:
        name = payload.get("tool_name") or payload.get("name")
        arguments = payload.get("arguments", {})
        if isinstance(name, str) and name and isinstance(arguments, dict):
            calls.append((name, arguments))
    return calls


def _clean_model_content(content: str | None) -> str:
    """Remove internal reasoning and tool-call markup from user-facing text."""
    text = content or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?(?:think|tool_call)>", "", text, flags=re.IGNORECASE)
    return text.strip()


def _validate_tool_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Validate common JSON Schema constraints before dispatching a model tool call."""
    if schema.get("type") not in (None, "object"):
        return "Tool input schema is not an object schema."
    if not isinstance(arguments, dict):
        return "Tool arguments must be a JSON object."

    required = schema.get("required", [])
    for key in required:
        if key not in arguments:
            return f"Missing required argument: {key}"

    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            return "Unknown argument(s): " + ", ".join(unknown)

    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for key, value in arguments.items():
        prop = properties.get(key, {})
        expected = prop.get("type") if isinstance(prop, dict) else None
        if isinstance(expected, list):
            expected_types = [type_map[item] for item in expected if item in type_map]
            if expected_types and not isinstance(value, tuple(expected_types)):
                return f"Argument '{key}' must match one of these types: {', '.join(expected)}."
        elif expected in type_map:
            expected_type = type_map[expected]
            if expected == "integer" and isinstance(value, bool):
                return f"Argument '{key}' must be an integer."
            if expected == "number" and isinstance(value, bool):
                return f"Argument '{key}' must be a number."
            if not isinstance(value, expected_type):
                return f"Argument '{key}' must be of type {expected}."
        if isinstance(prop, dict) and "enum" in prop and value not in prop["enum"]:
            return f"Argument '{key}' must be one of the allowed values."
    return None


def _tool_result_object(result: dict[str, Any]) -> dict[str, Any]:
    """Read structured MCP content or parse its JSON text representation."""
    structured = result.get("structured_content")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content") or []:
        if not isinstance(item, str):
            continue
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


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
        pending = self.pending.get(approval_id)
        if not pending:
            raise KeyError("Approval request was not found or has expired.")
        if not approved:
            self.pending.pop(approval_id, None)
            return {"status": "rejected", "message": "The operation was not executed."}

        kind = pending.get("kind", "write")
        if kind == "destructive":
            return {"status": "blocked", "message": "Destructive operations are disabled by policy."}

        arguments = dict(pending["arguments"])
        context = pending.get("approval_context")
        if context:
            try:
                # The token is created only after the approval endpoint receives
                # the human decision. It is never sent to the model.
                arguments["approval_token"] = mint_approval_token(
                    method=context["method"],
                    path=context["path"],
                    body=context.get("body"),
                    env_file=self.gateway.env_file,
                )
            except ApprovalSigningError as exc:
                return {"status": "blocked", "message": str(exc)}
        else:
            return {
                "status": "blocked",
                "message": "This gateway write operation has no verified approval-token mapping. Nothing was executed.",
            }

        # Consume the approval ID immediately before the one permitted execution.
        self.pending.pop(approval_id, None)
        result = await self.gateway.call_tool(pending["tool_name"], arguments)
        return {
            "status": "executed" if not result["is_error"] else "error",
            "tool": pending["tool_name"],
            "result": result,
            "verification": "The gateway accepted the operation response; independently verify the resulting device/resource state.",
        }

    async def run(self, message: str, model: str | None = None,
                  history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        # Keep the gateway subprocess alive for discovery, inspection, and execution
        # during this turn instead of spawning a new Python process per tool call.
        stack = AsyncExitStack()
        try:
            await stack.enter_async_context(self.gateway.connection())
        except Exception as exc:
            await stack.aclose()
            # Preserve general local-model chat when the gateway cannot start.
            logger.warning("NinjaOne MCP gateway unavailable for this turn: %s", exc)
            return await self._run_connected(message, model, history)
        async with stack:
            return await self._run_connected(message, model, history)

    async def _run_connected(self, message: str, model: str | None = None,
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
        tool_names = set(by_name)
        if "discover_endpoints" in tool_names:
            system_text += (
                " For NinjaOne API v2, use discover_endpoints, inspect_endpoint, then execute_endpoint. "
                "Use execute_paginated_endpoint only with documented pagination parameters. "
                "Never send approval_token; the backend injects it after human approval."
            )
        elif "find_endpoint" in tool_names:
            system_text += (
                " This gateway uses find_endpoint, describe_endpoint, call, and raw_call. "
                "Discover and describe an endpoint before calling it; never invent endpoint parameters."
            )
        if not available:
            system_text += (
                " The NinjaOne MCP gateway is currently unavailable or returned no tools. "
                "You may answer general questions, but do not claim to have retrieved live tenant data."
            )
        master_instructions = render_master_instructions()
        if master_instructions:
            system_text += "\n\n" + master_instructions
        skill_context = render_skill_context(message)
        if skill_context:
            system_text += "\n\n" + skill_context
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
                parsed_calls = _tool_calls_from_content(content) if available else []

                if parsed_calls:
                    # Qwen commonly emits XML-style tool calls in message content
                    # instead of OpenAI-compatible message.tool_calls.
                    messages.append({"role": "assistant", "content": content})
                    calls.extend((name, args, None) for name, args in parsed_calls)
                else:
                    parsed = _json_object(content) if used_fallback and available else None

                    # Some models accept the tools parameter but emit neither native
                    # tool_calls nor Qwen markup. Ask for one explicit tool plan.
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
                                        "Decide whether this request needs live NinjaOne data or an operation. "
                                        "If yes, return ONLY a tool call as "
                                        '<tool_call>{"name":"exact_name","arguments":{}}</tool_call>. '
                                        "If no tool is needed, answer normally. Available tools: " + tool_reference
                                    ),
                                }],
                            )
                            fallback_content = fallback.choices[0].message.content or ""
                            parsed_calls = _tool_calls_from_content(fallback_content)
                            if parsed_calls:
                                content = fallback_content
                                messages.append({"role": "assistant", "content": content})
                                calls.extend((name, args, None) for name, args in parsed_calls)
                            else:
                                parsed = _json_object(fallback_content)
                                if parsed:
                                    content = fallback_content
                        except FoundryUnavailable:
                            logger.warning("Tool-planning fallback is unavailable for model %s", selected)

                    if not calls:
                        if parsed and isinstance(parsed.get("tool_name") or parsed.get("name"), str):
                            messages.append({"role": "assistant", "content": content})
                            args = parsed.get("arguments") or {}
                            calls.append((parsed.get("tool_name") or parsed.get("name"),
                                          args if isinstance(args, dict) else {}, None))
                        else:
                            safe_answer = _clean_model_content(content)
                            if not safe_answer:
                                safe_answer = (
                                    "I couldn't execute a valid NinjaOne tool call for this request. "
                                    "Please try again; no live tenant data was retrieved."
                                    if available else
                                    "The NinjaOne MCP gateway is unavailable, so I couldn't retrieve live tenant data."
                                )
                            return {"model": selected, "answer": safe_answer, "steps": transcript,
                                    "pending_approvals": []}

            for tool_name, arguments, call_id in calls:
                tool = by_name.get(tool_name)
                if not tool:
                    transcript.append({"tool": tool_name, "status": "rejected", "error": "Unknown MCP tool name."})
                    messages.append({"role": "system", "content": f"Tool {tool_name} does not exist. Available tools: {', '.join(by_name)}"})
                    continue

                validation_error = _validate_tool_arguments(
                    tool.get("input_schema") or {}, arguments
                )
                if validation_error:
                    transcript.append({
                        "tool": tool_name,
                        "status": "rejected",
                        "error": validation_error,
                    })
                    messages.append({
                        "role": "system",
                        "content": f"Invalid arguments for {tool_name}: {validation_error}. Correct the arguments before retrying.",
                    })
                    continue

                # Never accept an approval token generated by the model.
                arguments = dict(arguments)
                arguments.pop("approval_token", None)
                approval_context = None
                classification_arguments = dict(arguments)

                if tool_name == "execute_endpoint":
                    endpoint_key = arguments.get("endpoint")
                    if not isinstance(endpoint_key, str):
                        transcript.append({"tool": tool_name, "status": "rejected",
                                           "error": "A discovered endpoint key is required."})
                        continue
                    try:
                        inspected = await self.gateway.call_tool(
                            "inspect_endpoint", {"endpoint": endpoint_key}
                        )
                        detail = _tool_result_object(inspected)
                        method = str(detail.get("method") or "").upper()
                        template = detail.get("path")
                        if inspected.get("is_error") or method not in {"GET", "POST", "PUT", "PATCH"} or not isinstance(template, str):
                            raise ValueError("Could not verify the endpoint method and path from inspect_endpoint.")
                        resolved = resolve_path(template, arguments.get("path_params") or {})
                        approval_context = {
                            "method": method, "path": resolved, "body": arguments.get("body")
                        }
                        classification_arguments["http_method"] = method
                    except Exception as exc:
                        transcript.append({"tool": tool_name, "status": "rejected",
                                           "error": f"Endpoint inspection failed: {exc}"})
                        messages.append({"role": "system", "content": f"Do not execute {endpoint_key}; endpoint inspection failed: {exc}"})
                        continue
                elif tool_name == "execute_raw_request":
                    raw_request = arguments.get("request", "")
                    parts = raw_request.strip().split(None, 1) if isinstance(raw_request, str) else []
                    if len(parts) != 2:
                        transcript.append({"tool": tool_name, "status": "rejected",
                                           "error": "Expected METHOD /relative/path."})
                        continue
                    method = parts[0].upper()
                    parsed_url = urlsplit(parts[1])
                    if method not in {"GET", "POST", "PUT", "PATCH"} or parsed_url.scheme or parsed_url.netloc or not parsed_url.path.startswith("/"):
                        transcript.append({"tool": tool_name, "status": "rejected",
                                           "error": "Only supported relative NinjaOne API paths can be executed."})
                        continue
                    approval_context = {
                        "method": method, "path": parsed_url.path, "body": arguments.get("body")
                    }
                    classification_arguments["http_method"] = method
                elif tool_name == "execute_paginated_endpoint":
                    classification_arguments["http_method"] = "GET"

                kind = classify_tool(tool, classification_arguments)
                allowed, decision = approval_decision(
                    tool, self.settings.allow_delete_operations, classification_arguments
                )
                if kind == "write" and not self.settings.approval_required_for_writes:
                    # Do not permit a config flag to bypass the gateway's HMAC approval.
                    allowed, decision = False, "approval_required"
                if not allowed:
                    if decision == "blocked":
                        transcript.append({"tool": tool_name, "status": "blocked",
                                           "message": "Destructive operation is disabled by configuration."})
                        messages.append({"role": "system", "content": f"{tool_name} is blocked by policy. Do not retry it."})
                        continue
                    if kind == "write" and approval_context is None:
                        transcript.append({"tool": tool_name, "status": "blocked",
                                           "error": "No trusted method/path/body approval mapping exists for this write."})
                        messages.append({"role": "system", "content": f"{tool_name} cannot be safely approved by this backend. Do not retry it."})
                        continue
                    approval_id = str(uuid.uuid4())
                    self.pending[approval_id] = {
                        "tool": tool, "tool_name": tool_name, "arguments": arguments,
                        "approval_context": approval_context, "kind": kind,
                    }
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
        return {"model": selected, "answer": _clean_model_content(final.choices[0].message.content),
                "steps": transcript, "pending_approvals": []}
