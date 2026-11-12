import json
from difflib import get_close_matches
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
from ninjaone_agent.cache import TTLCache
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


def _likely_live_request(message: str) -> bool:
    """Avoid a second planning completion for ordinary chat, but keep fuzzy RMM asks tool-first."""
    text = (message or "").casefold()
    indicators = (
        "ninjaone", "organization", "organisations", "orgs", "customer", "location",
        "site", "group", "device", "endpoint", "laptop", "machine", "computer",
        "server", "workstation", "user", "technician", "alert", "incident",
        "activity", "ticket", "policy", "patch", "update", "script", "automation",
        "reboot", "restart", "shutdown", "service", "offline", "online",
        "check in", "check-in", "last seen", "inventory", "asset", "software",
        "hardware", "disk", "storage", "memory", "cpu", "compliance", "antivirus",
        "backup", "report", "job", "task", "remediation", "fix", "repair",
        "assign", "how many", "show me", "find all", "fleet", "attention", "unhealthy",
        "health", "at risk", "at-risk", "noncompliant", "non-compliant", "not compliant",
        "issue", "issues", "problem", "problems", "failing", "failed", "failure",
        "down", "not working", "broken", "trouble", "troubleshoot", "what is wrong",
    )
    return any(term in text for term in indicators)


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


def _tool_failed(result: dict[str, Any]) -> bool:
    """Treat gateway-level error payloads as failures even when MCP isError is false."""
    if result.get("is_error"):
        return True
    payload = _tool_result_object(result)
    return bool(payload.get("error")) or payload.get("executed") is False


def _direct_inventory_intent(message: str) -> dict[str, str] | None:
    """Recognize clear inventory list/count requests, including common fuzzy wording."""
    text = re.sub(r"\s+", " ", (message or "").casefold()).strip()
    if not text:
        return None

    # Filtered, investigative, or state-dependent requests must use the full agent.
    filter_terms = (
        r"\b(where|whose|matching|details?|status|named|called|offline|online|inactive|"
        r"active|stale|unhealthy|not reporting|not communicating|last seen|last check.?in|"
        r"older than|newer than|with|without|in|for|under|since|before|after|by|serial|asset tag)\b"
    )
    if re.search(filter_terms, text):
        return None

    count = bool(re.search(r"\b(count|total|how many|number of)\b", text))
    list_intent = bool(re.search(
        r"\b(list|show|display|get|fetch|retrieve|pull|give me)\b", text
    ))
    list_intent = list_intent or bool(re.search(r"\bfind\s+all\b", text))
    list_intent = list_intent or bool(re.search(r"\bwhat\s+(?:are|is|do|does)\b", text))
    list_intent = list_intent or bool(re.search(r"\bwhich\s+(?:all\s+)?[a-z0-9-]+", text))

    aliases = {
        "organizations": ("organization", "organizations", "org", "orgs", "customer", "customers"),
        "technicians": ("technician", "technicians", "tech", "techs"),
        "devices": ("device", "devices", "endpoint", "endpoints", "laptop", "laptops",
                    "server", "servers", "workstation", "workstations", "machine", "machines",
                    "computer", "computers", "pc", "pcs"),
        "users": ("user", "users", "employee", "employees", "enduser", "endusers"),
        "locations": ("location", "locations", "site", "sites"),
        "groups": ("group", "groups"),
    }
    tokens = re.findall(r"[a-z0-9]+", text)
    matches: list[str] = []
    for resource, terms in aliases.items():
        for token in tokens:
            # Conservative typo tolerance: only match a resource alias, never an endpoint/action.
            if token in terms or get_close_matches(token, terms, n=1, cutoff=0.84):
                matches.append(resource)
                break

    if len(set(matches)) != 1:
        return None
    if not (count or list_intent):
        # Accept short noun phrases such as "all machines" or "PC inventory".
        if not re.fullmatch(
            r"(?:please\s+)?(?:all\s+)?[a-z0-9\s-]+(?:[?.!]*)", text
        ):
            return None
        if not any(word in text for word in ("all", "inventory", "names", "list", "count")):
            return None
    return {"resource": matches[0], "operation": "count" if count else "list"}


def _people_name_search_intent(message: str) -> str | None:
    """Extract an explicit name filter for cross-category people searches."""
    text = re.sub(r"\s+", " ", (message or "")).strip()
    patterns = (
        r"\b(?:with|whose)\s+(?:first\s+|last\s+|full\s+)?name(?:\s+(?:contains?|is|starts?\s+with))?\s+['\"“]?([^'\"”?.!,;]+)",
        r"\b(?:users?|technicians?|contacts?|people)\s+(?:named|called)\s+['\"“]?([^'\"”?.!,;]+)",
        r"\bname\s+contains?\s+['\"“]?([^'\"”?.!,;]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            name = match.group(1).strip().strip(" '\"“”")
            if len(name) >= 2:
                return name
    return None


def _record_matches_person_name(record: dict[str, Any], query: str) -> bool:
    """Match a name against common NinjaOne person fields without guessing identity."""
    first = str(record.get("firstName") or record.get("first_name") or "")
    last = str(record.get("lastName") or record.get("last_name") or "")
    values = (
        first, last, (first + " " + last).strip(),
        str(record.get("name") or ""),
        str(record.get("displayName") or record.get("display_name") or ""),
        str(record.get("username") or ""),
        str(record.get("email") or ""),
    )
    needle = query.casefold()
    return any(needle in value.casefold() for value in values if value)


def _walk_json(value: Any):
    """Yield nested dictionaries/lists from parsed MCP output."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _tool_payloads(result: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    structured = result.get("structured_content")
    if structured is not None:
        values.append(structured)
    for item in result.get("content") or []:
        if isinstance(item, str):
            try:
                values.append(json.loads(item))
            except json.JSONDecodeError:
                continue
    return values


def _endpoint_candidate(
    result: dict[str, Any], resource: str | None = None
) -> dict[str, Any] | None:
    """Select the best documented GET endpoint for a resource from discovery results."""
    candidates: list[tuple[int, dict[str, Any]]] = []
    resource_term = (resource or "").casefold().rstrip("s")
    for payload in _tool_payloads(result):
        for item in _walk_json(payload):
            method = str(item.get("method") or item.get("http_method") or "").upper()
            path = item.get("path") or item.get("endpoint_path")
            endpoint = item.get("endpoint")
            if not (method == "GET" and isinstance(path, str) and path.startswith("/")):
                if isinstance(endpoint, str):
                    match = re.match(r"^GET\s+(/\S+)", endpoint.strip(), re.IGNORECASE)
                    if not match:
                        continue
                    method, path = "GET", match.group(1)
                else:
                    continue
            candidate = dict(item)
            candidate.setdefault("method", method)
            candidate.setdefault("path", path)
            searchable = " ".join(str(candidate.get(key) or "")
                                  for key in ("path", "endpoint", "summary", "description")).casefold()
            score = 0
            if resource_term and resource_term in searchable:
                score += 10
            if resource and resource.casefold() in searchable:
                score += 5
            path_text = str(candidate.get("path") or "").casefold()
            if resource_term and resource_term in path_text:
                score += 5
            path_segments = [segment for segment in path_text.strip("/").split("/") if segment]
            if resource_term and path_segments and path_segments[-1] in {
                resource_term, resource_term + "s"
            }:
                score += 20
            # Prefer collection endpoints over organization/device-scoped endpoints.
            score -= path_text.count("{") * 20
            candidates.append((score, candidate))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _inventory_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract records from common NinjaOne collection response envelopes."""
    wrappers = ("items", "data", "results", "records", "organizations", "devices",
                "technicians", "users", "locations", "groups", "contacts", "endUsers", "end_users")
    for payload in _tool_payloads(result):
        for item in _walk_json(payload):
            for key in wrappers:
                value = item.get(key)
                if isinstance(value, list):
                    return [record for record in value if isinstance(record, dict)]
            if isinstance(payload, list):
                return [record for record in payload if isinstance(record, dict)]
    return []


def _inventory_total(result: dict[str, Any]) -> int | None:
    """Use an explicit total/count field when the API paginates its collection."""
    keys = ("total", "count", "totalCount", "total_count", "resultCount", "recordsTotal")
    for payload in _tool_payloads(result):
        for item in _walk_json(payload):
            for key in keys:
                value = item.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
    return None


def _tool_arguments(tool: dict[str, Any], preferred: dict[str, Any]) -> dict[str, Any] | None:
    """Build only arguments accepted by the currently connected gateway schema."""
    schema = tool.get("input_schema") or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    arguments = {
        key: value for key, value in preferred.items()
        if key in properties and value is not None
    }
    if any(key not in arguments for key in required):
        return None
    if _validate_tool_arguments(schema, arguments):
        return None
    return arguments


class NinjaOneAgent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.foundry = FoundryClient(settings)
        self.gateway = NinjaOneMCPGateway(settings)
        # Cache tool schemas briefly; refresh automatically to pick up gateway changes.
        self._tool_cache: TTLCache[list[dict[str, Any]]] = TTLCache(ttl_seconds=30, max_entries=2)
        # Cache only verified GET route metadata, never live tenant response data.
        self._endpoint_cache: TTLCache[dict[str, Any]] = TTLCache(ttl_seconds=900, max_entries=64)
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
        if not context:
            return {
                "status": "blocked",
                "message": "This gateway write operation has no verified approval mapping. Nothing was executed.",
            }
        if context.get("inject_token"):
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

        # Consume the approval ID immediately before the one permitted execution.
        self.pending.pop(approval_id, None)
        result = await self.gateway.call_tool(pending["tool_name"], arguments)
        return {
            "status": "error" if _tool_failed(result) else "executed",
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


    def _format_structured_result(
        self, model: str, request: str, records: list[dict[str, Any]], fallback: str
    ) -> str:
        """Use the local LLM to present structured records; retain a safe fallback."""
        if not records or len(records) > 30:
            return fallback
        try:
            response = self.foundry.complete(
                model=model,
                messages=[
                    {"role": "system", "content": (
                        "Format verified NinjaOne API results for an IT administrator. "
                        "Return clean Markdown only, never raw JSON or code fences. "
                        "Do not invent, infer, rename, or alter values. Preserve every record "
                        "and distinguish duplicate names by category and ID. Use a short heading "
                        "and count, then numbered compact record sections with readable labels. "
                        "Use tables only when useful. Omit null, empty, internal, and irrelevant "
                        "fields. Never expose tokens, secrets, MFA seeds, or credentials. "
                        "Keep IDs and values exact."
                    )},
                    {"role": "user", "content": (
                        "User request: " + request + "\nVerified API records:\n"
                        + json.dumps(records, ensure_ascii=False, default=str)
                    )},
                ],
            )
            answer = _clean_model_content(response.choices[0].message.content)
            if answer and not _tool_calls_from_content(answer) and not _json_object(answer):
                return answer
        except Exception:
            logger.exception("Local LLM result formatting failed; using deterministic formatting.")
        return fallback

    @staticmethod
    def _format_records_fallback(records: list[dict[str, Any]], heading: str) -> str:
        """Readable Markdown fallback that never depends on another model call."""
        lines = [f"### {heading}", f"**{len(records)} record(s)**"]
        hidden = {"uid", "matched_category"}
        for index, record in enumerate(records, start=1):
            name = (" ".join(str(record.get(key)) for key in ("firstName", "lastName")
                             if record.get(key)) or record.get("displayName")
                    or record.get("name") or record.get("email") or f"Record {index}")
            category = record.get("matched_category") or record.get("userType")
            lines.extend(["", f"**{index}. {name}**" + (f" — {category}" if category else "")])
            for key, value in record.items():
                if key in hidden or value in (None, "", [], {}):
                    continue
                label = re.sub(r"([a-z])([A-Z])", r"\1 \2", key).replace("_", " ").capitalize()
                if isinstance(value, bool):
                    value = "Yes" if value else "No"
                elif isinstance(value, list):
                    value = ", ".join(str(item) for item in value)
                lines.append(f"- **{label}:** {value}")
        return "\n".join(lines)

    async def _run_direct_inventory(
        self, intent: dict[str, str], by_name: dict[str, dict[str, Any]], model: str
    ) -> dict[str, Any] | None:
        """Execute a simple inventory GET, reusing verified endpoint metadata when cached."""
        resource = intent["resource"]
        steps: list[dict[str, Any]] = []
        cached_route = self._endpoint_cache.get(resource)

        if cached_route:
            candidate = cached_route["candidate"]
            detail = cached_route["detail"]
            logger.info("Endpoint metadata cache hit for inventory resource %s.", resource)
        else:
            discovery = by_name["find_endpoint"]
            discovery_args = _tool_arguments(discovery, {"query": resource})
            if discovery_args is None:
                return None
            found = await self.gateway.call_tool("find_endpoint", discovery_args)
            steps.append({"tool": "find_endpoint",
                          "status": "error" if _tool_failed(found) else "completed",
                          "result": found})
            if _tool_failed(found):
                return {"answer": "NinjaOne endpoint discovery failed. No inventory data was retrieved.",
                        "steps": steps, "pending_approvals": []}

            candidate = _endpoint_candidate(found, resource)
            if not candidate:
                return None
            endpoint = candidate.get("endpoint") or f"{candidate['method']} {candidate['path']}"

            describe = by_name["describe_endpoint"]
            describe_args = _tool_arguments(describe, {"endpoint": endpoint})
            if describe_args is None:
                return None
            inspected = await self.gateway.call_tool("describe_endpoint", describe_args)
            steps.append({"tool": "describe_endpoint",
                          "status": "error" if _tool_failed(inspected) else "completed",
                          "result": inspected})
            if _tool_failed(inspected):
                return {"answer": "NinjaOne endpoint inspection failed. No inventory data was retrieved.",
                        "steps": steps, "pending_approvals": []}
            detail = _tool_result_object(inspected)

        method = str(detail.get("method") or candidate.get("method") or "").upper()
        path = detail.get("path") or candidate.get("path")
        if method != "GET" or not isinstance(path, str) or not path.startswith("/"):
            # A stale or malformed route is discarded; do not attempt execution.
            self._endpoint_cache.clear()
            return None

        endpoint_key = detail.get("endpoint") or candidate.get("endpoint") or f"GET {path}"
        # Simple inventory requests cannot safely call endpoints requiring an unknown
        # organization/device/location ID. Let the full agent resolve scoped resources.
        if re.search(r"\{[^}]+\}", path):
            return None
        # Cache only after both discovery and description established a documented GET.
        if not cached_route:
            self._endpoint_cache.set(resource, {
                "candidate": candidate,
                "detail": detail,
            })

        call_tool = by_name["call"]
        call_args = _tool_arguments(call_tool, {
            "endpoint": endpoint_key, "path_params": {}, "query": {}, "body": None
        })
        if call_args is None:
            return None
        result = await self.gateway.call_tool("call", call_args)
        steps.append({"tool": "call", "status": "error" if _tool_failed(result) else "completed",
                      "result": result})
        if _tool_failed(result):
            return {"answer": f"NinjaOne returned an error while retrieving {resource}.",
                    "steps": steps, "pending_approvals": []}

        records = _inventory_records(result)
        if intent["operation"] == "count":
            total = _inventory_total(result)
            count = total if total is not None else len(records)
            answer = f"NinjaOne returned {count} {resource} record(s)."
        elif records:
            fallback = self._format_records_fallback(records[:30], resource.title())
            answer = self._format_structured_result(
                model, f"{intent['operation']} {resource}",
                records[:30], fallback
            )
        else:
            answer = "NinjaOne returned a response, but no recognizable records were present."
        return {"answer": answer, "steps": steps, "pending_approvals": []}

    async def _run_people_name_search(
        self, name: str, by_name: dict[str, dict[str, Any]], model: str
    ) -> dict[str, Any] | None:
        """Search all exposed people collections and filter the live records by name."""
        required = {"find_endpoint", "describe_endpoint", "call"}
        if not required.issubset(by_name):
            return None

        categories = (
            ("console users", "users"),
            ("end users", "end-users"),
            ("technicians", "technicians"),
            ("contacts", "contacts"),
        )
        steps: list[dict[str, Any]] = []
        matches: list[dict[str, Any]] = []
        searched: list[str] = []
        unavailable: list[str] = []
        seen: set[tuple[str, str]] = set()

        for label, query in categories:
            discovery_args = _tool_arguments(by_name["find_endpoint"], {"query": query})
            if discovery_args is None:
                unavailable.append(label)
                continue
            found = await self.gateway.call_tool("find_endpoint", discovery_args)
            steps.append({"tool": "find_endpoint", "category": label,
                          "status": "error" if _tool_failed(found) else "completed",
                          "result": found})
            if _tool_failed(found):
                unavailable.append(label)
                continue

            candidate = _endpoint_candidate(found, query)
            if not candidate:
                unavailable.append(label)
                continue
            # Avoid treating a singular/detail endpoint (for example an app-user-contact
            # lookup) as a complete collection of people.
            expected_collections = {
                "console users": {"users"},
                "end users": {"end-users"},
                "technicians": {"technicians"},
                "contacts": {"contacts"},
            }
            candidate_path = str(candidate.get("path") or "").rstrip("/")
            final_segment = candidate_path.split("/")[-1].casefold()
            if final_segment not in expected_collections[label]:
                unavailable.append(label)
                continue
            endpoint = candidate.get("endpoint") or (
                f"{candidate.get('method')} {candidate.get('path')}"
            )
            inspect_args = _tool_arguments(
                by_name["describe_endpoint"], {"endpoint": endpoint}
            )
            if inspect_args is None:
                unavailable.append(label)
                continue
            inspected = await self.gateway.call_tool("describe_endpoint", inspect_args)
            steps.append({"tool": "describe_endpoint", "category": label,
                          "status": "error" if _tool_failed(inspected) else "completed",
                          "result": inspected})
            if _tool_failed(inspected):
                unavailable.append(label)
                continue

            detail = _tool_result_object(inspected)
            method = str(detail.get("method") or candidate.get("method") or "").upper()
            path = detail.get("path") or candidate.get("path")
            if method != "GET" or not isinstance(path, str) or not path.startswith("/") or "{" in path:
                unavailable.append(label)
                continue

            call_args = _tool_arguments(by_name["call"], {
                "endpoint": detail.get("endpoint") or endpoint,
                "path_params": {}, "query": {}, "body": None,
            })
            if call_args is None:
                unavailable.append(label)
                continue
            result = await self.gateway.call_tool("call", call_args)
            steps.append({"tool": "call", "category": label,
                          "status": "error" if _tool_failed(result) else "completed",
                          "result": result})
            if _tool_failed(result):
                unavailable.append(label)
                continue

            searched.append(label)
            records = _inventory_records(result)
            reported_total = _inventory_total(result)
            if reported_total is not None and reported_total > len(records):
                unavailable.append(f"{label} (only {len(records)} of {reported_total} records returned; pagination required)")
            for record in records:
                if not _record_matches_person_name(record, name):
                    continue
                record_copy = dict(record)
                record_copy["matched_category"] = label
                identity = str(record.get("id") or record.get("uid") or record.get("email") or "")
                key = (label, identity or json.dumps(record, sort_keys=True, default=str))
                if key not in seen:
                    seen.add(key)
                    matches.append(record_copy)

        if not searched:
            return {
                "answer": "I could not retrieve any NinjaOne people collections, so I cannot confirm the name search. No operation was executed.",
                "steps": steps, "pending_approvals": [],
            }
        if matches:
            fallback = self._format_records_fallback(matches, f"People matching '{name}'")
            answer = self._format_structured_result(
                model,
                f"Find people matching the name {name}. Categories searched: {', '.join(searched)}.",
                matches, fallback,
            )
        else:
            answer = f"No matching people records containing '{name}' were found in the retrieved categories: " + ", ".join(searched) + "."
        if unavailable:
            answer += " Search coverage is incomplete; these categories could not be queried: " + ", ".join(unavailable) + "."
        return {"answer": answer, "steps": steps, "pending_approvals": []}

    async def _run_connected(self, message: str, model: str | None = None,
                             history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        selected = self.foundry.resolve_model(model)
        try:
            available = self._tool_cache.get("mcp_tools")
            if available is None:
                available = await self.gateway.list_tools()
                self._tool_cache.set("mcp_tools", available)
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
        prior_context = " ".join(
            item.get("content", "") for item in (history or [])[-4:]
            if item.get("role") in ("user", "assistant")
        )
        live_request = _likely_live_request(prior_context + " " + message)
        if live_request and not available:
            return {
                "model": selected,
                "answer": "The NinjaOne MCP gateway is unavailable. No live tenant data was retrieved and no operation was executed.",
                "steps": [],
                "pending_approvals": [],
            }

        # Fast path: simple inventory lists/counts use endpoint discovery + one GET
        # directly. Complex, filtered, and all write requests stay on the full agent path.
        people_name = _people_name_search_intent(message)
        if people_name and {"find_endpoint", "describe_endpoint", "call"}.issubset(tool_names):
            logger.info("Using cross-category people name search for %r.", people_name)
            people_result = await self._run_people_name_search(people_name, by_name, selected)
            if people_result is not None:
                people_result["model"] = selected
                return people_result

        direct_intent = _direct_inventory_intent(message)
        if direct_intent and {"find_endpoint", "describe_endpoint", "call"}.issubset(tool_names):
            logger.info("Using direct read-only inventory path for %s (%s).",
                        direct_intent["resource"], direct_intent["operation"])
            direct_result = await self._run_direct_inventory(direct_intent, by_name, selected)
            if direct_result is not None:
                direct_result["model"] = selected
                return direct_result
        skill_query = (prior_context + " " + message).strip()
        skill_context = render_skill_context(skill_query)
        if skill_context:
            system_text += "\n\n" + skill_context
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
        for item in (history or [])[-8:]:
            if item.get("role") in ("user", "assistant") and item.get("content"):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": message})

        tool_specs = _openai_tools(available)
        transcript: list[dict[str, Any]] = []
        endpoint_details: dict[str, dict[str, Any]] = {}
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
                    if not parsed and not used_fallback and available and live_request:
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
                            if live_request:
                                # A failed tool-planning round is not a reason to abandon the
                                # conversation. Ask the model for a user-facing clarification
                                # grounded in the request and any discovery results already seen.
                                # This completion has no tools, so it cannot execute an operation.
                                try:
                                    clarification = self.foundry.complete(
                                        model=selected,
                                        messages=messages + [{
                                            "role": "system",
                                            "content": (
                                                "You are now in clarification mode. Do not call tools, "
                                                "propose JSON, or claim that an operation was executed. "
                                                "Review the original user request and the complete conversation, "
                                                "including any MCP discovery results and errors. If a required "
                                                "detail is missing, ask the smallest set of specific questions "
                                                "needed to continue. If multiple interpretations would lead to "
                                                "different NinjaOne resources or permissions, explain the "
                                                "distinction briefly and ask the user to choose. Do not ask for "
                                                "details that can safely be resolved by read-only API discovery. "
                                                "If the request is already clear but discovery failed, explain "
                                                "what failed and what information or access is needed. Be "
                                                "transparent that no write was performed."
                                            ),
                                        }],
                                    )
                                    safe_answer = _clean_model_content(
                                        clarification.choices[0].message.content
                                    )
                                    if (not safe_answer or _tool_calls_from_content(safe_answer)
                                            or _json_object(safe_answer)):
                                        safe_answer = (
                                            "I need a little more information before I can safely continue. "
                                            "No change has been made. Please specify the target user/resource "
                                            "and the intended group, policy, or access change."
                                        )
                                except Exception:
                                    logger.exception("Clarification response generation failed.")
                                    safe_answer = (
                                        "I couldn't complete the NinjaOne request safely. "
                                        "No change was made. Please provide the target and the intended "
                                        "group, policy, or access change so I can narrow the operation."
                                    )
                            else:
                                safe_answer = _clean_model_content(content) or "I couldn't generate a response."
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

                if tool_name in {"execute_endpoint", "call"}:
                    endpoint_key = arguments.get("endpoint")
                    if not isinstance(endpoint_key, str):
                        transcript.append({"tool": tool_name, "status": "rejected",
                                           "error": "A discovered endpoint key is required."})
                        continue
                    try:
                        describe_name = (
                            "inspect_endpoint" if tool_name == "execute_endpoint"
                            else "describe_endpoint"
                        )
                        detail = endpoint_details.get(endpoint_key, {})
                        if not detail and describe_name in by_name:
                            inspected = await self.gateway.call_tool(
                                describe_name, {"endpoint": endpoint_key}
                            )
                            detail = _tool_result_object(inspected)

                        # Legacy call() accepts "METHOD /path"; current
                        # execute_endpoint() requires the inspected endpoint key.
                        parts = endpoint_key.strip().split(None, 1)
                        method = str(detail.get("method") or (
                            parts[0] if len(parts) == 2 else ""
                        )).upper()
                        template = detail.get("path") or (
                            parts[1] if len(parts) == 2 else None
                        )
                        if method not in {"GET", "POST", "PUT", "PATCH"} or not isinstance(template, str):
                            raise ValueError("Could not verify the endpoint method and path.")
                        resolved = resolve_path(template, arguments.get("path_params") or {})
                        supports_token = (
                            "approval_token" in (tool.get("input_schema") or {}).get("properties", {})
                        )
                        approval_context = {
                            "method": method,
                            "path": resolved,
                            "body": arguments.get("body"),
                            "inject_token": tool_name == "execute_endpoint" or supports_token,
                        }
                        classification_arguments["http_method"] = method
                    except Exception as exc:
                        transcript.append({"tool": tool_name, "status": "rejected",
                                           "error": f"Endpoint inspection failed: {exc}"})
                        messages.append({"role": "system", "content": f"Do not execute {endpoint_key}; endpoint inspection failed: {exc}"})
                        continue
                elif tool_name in {"execute_raw_request", "raw_call"}:
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
                    supports_token = (
                        "approval_token" in (tool.get("input_schema") or {}).get("properties", {})
                    )
                    approval_context = {
                        "method": method,
                        "path": parsed_url.path,
                        "body": arguments.get("body"),
                        "inject_token": tool_name == "execute_raw_request" or supports_token,
                    }
                    classification_arguments["http_method"] = method
                elif tool_name == "execute_paginated_endpoint":
                    classification_arguments["http_method"] = "GET"
                elif tool_name == "memory_save":
                    # This changes local agent memory, not the NinjaOne tenant.
                    approval_context = {"inject_token": False}

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
                    if tool_name in {"inspect_endpoint", "describe_endpoint"}:
                        inspected_key = arguments.get("endpoint")
                        inspected_data = _tool_result_object(result)
                        if isinstance(inspected_key, str) and inspected_data and not _tool_failed(result):
                            endpoint_details[inspected_key] = inspected_data
                    transcript.append({"tool": tool_name, "status": "error" if _tool_failed(result) else "completed",
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
