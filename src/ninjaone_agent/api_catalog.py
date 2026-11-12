"""Local, read-only search over a normalized NinjaOne OpenAPI catalogue.

The catalogue contains API documentation only. It never caches tenant data and
never executes requests. The MCP gateway remains the only execution boundary.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
_SAFE_METHODS = {"GET"}
_SENSITIVE_FIELD_PARTS = ("password", "secret", "token", "credential", "privatekey")


def _resolve_ref(spec: dict[str, Any], schema: Any, seen: set[str] | None = None) -> Any:
    """Resolve local JSON Schema references while preventing recursive loops."""
    if not isinstance(schema, dict):
        return schema
    seen = seen or set()
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/") and ref not in seen:
        target: Any = spec
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            return {"$ref": ref}
        resolved = _resolve_ref(spec, target, seen | {ref})
        siblings = {k: v for k, v in schema.items() if k != "$ref"}
        if isinstance(resolved, dict):
            return {**resolved, **siblings}
        return resolved
    result = dict(schema)
    for key in ("properties", "items", "additionalProperties", "not"):
        if key in result:
            value = result[key]
            if key == "properties" and isinstance(value, dict):
                result[key] = {name: _resolve_ref(spec, child, seen) for name, child in value.items()}
            else:
                result[key] = _resolve_ref(spec, value, seen)
    for key in ("allOf", "oneOf", "anyOf"):
        if isinstance(result.get(key), list):
            result[key] = [_resolve_ref(spec, child, seen) for child in result[key]]
    return result


def _field_details(schema: Any, prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
    """Flatten object fields into compact dotted paths with types/descriptions."""
    if not isinstance(schema, dict) or depth > 5:
        return []
    fields: list[dict[str, Any]] = []
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = set(schema.get("required") or [])
        for name, child in properties.items():
            if not isinstance(child, dict):
                continue
            path = f"{prefix}.{name}" if prefix else name
            field_type = child.get("type")
            if not field_type:
                if "enum" in child:
                    field_type = "enum"
                elif "properties" in child:
                    field_type = "object"
                elif "items" in child:
                    field_type = "array"
                else:
                    field_type = "unknown"
            description = re.sub(r"\s+", " ", str(child.get("description") or "")).strip()
            fields.append({
                "name": path,
                "type": field_type,
                "description": description[:240],
                "required": name in required,
                "enum": child.get("enum") if isinstance(child.get("enum"), list) else None,
            })
            nested = child
            if child.get("type") == "array" and isinstance(child.get("items"), dict):
                nested = child["items"]
            if isinstance(nested.get("properties"), dict):
                fields.extend(_field_details(nested, path, depth + 1))
    return fields


def _operation_fields(spec: dict[str, Any], operation: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parameters: list[dict[str, Any]] = []
    for param in operation.get("parameters") or []:
        resolved = _resolve_ref(spec, param)
        if not isinstance(resolved, dict):
            continue
        schema = _resolve_ref(spec, resolved.get("schema") or {})
        parameters.append({
            "name": resolved.get("name", ""),
            "in": resolved.get("in", ""),
            "required": bool(resolved.get("required")),
            "type": schema.get("type", "unknown") if isinstance(schema, dict) else "unknown",
            "description": str(resolved.get("description") or "")[:240],
        })

    body = operation.get("requestBody")
    if body:
        body = _resolve_ref(spec, body)
        content = body.get("content", {}) if isinstance(body, dict) else {}
        for media in content.values():
            schema = _resolve_ref(spec, media.get("schema") or {}) if isinstance(media, dict) else {}
            parameters.extend({
                "name": field["name"], "in": "body", "required": field["required"],
                "type": field["type"], "description": field["description"],
            } for field in _field_details(schema))

    response_fields: list[dict[str, Any]] = []
    responses = operation.get("responses") or {}
    for status, response in responses.items():
        if not str(status).startswith("2"):
            continue
        resolved = _resolve_ref(spec, response)
        content = resolved.get("content", {}) if isinstance(resolved, dict) else {}
        for media in content.values():
            schema = _resolve_ref(spec, media.get("schema") or {}) if isinstance(media, dict) else {}
            candidate = _field_details(schema)
            if len(candidate) > len(response_fields):
                response_fields = candidate
    return parameters, response_fields


def build_catalog(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAPI 3 document into a compact endpoint/field catalogue."""
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
        raise ValueError("The downloaded document is not a valid OpenAPI document with paths.")
    endpoints: list[dict[str, Any]] = []
    for path, path_item in spec["paths"].items():
        if not isinstance(path_item, dict):
            continue
        common_parameters = path_item.get("parameters") or []
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            merged = dict(operation)
            merged["parameters"] = list(common_parameters) + list(operation.get("parameters") or [])
            parameters, response_fields = _operation_fields(spec, merged)
            tags = operation.get("tags") or []
            summary = re.sub(r"\s+", " ", str(operation.get("summary") or "")).strip()
            description = re.sub(r"\s+", " ", str(operation.get("description") or "")).strip()
            endpoints.append({
                "method": method.upper(),
                "path": path,
                "operation_id": operation.get("operationId", ""),
                "summary": summary,
                "description": description[:1000],
                "tags": [str(tag) for tag in tags],
                "parameters": parameters,
                "response_fields": response_fields,
            })
    endpoints.sort(key=lambda item: (item["path"], item["method"]))
    info = spec.get("info") or {}
    return {
        "catalog_version": 1,
        "source": "NinjaOne Public API v2 OpenAPI",
        "api_version": str(info.get("version") or ""),
        "title": str(info.get("title") or "NinjaOne Public API"),
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
    }


class NinjaOneAPICatalog:
    """Small in-memory search index over local OpenAPI metadata."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._endpoints: list[dict[str, Any]] = []
        self.loaded = False
        self.load()

    def load(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            endpoints = payload.get("endpoints") if isinstance(payload, dict) else None
            if not isinstance(endpoints, list):
                raise ValueError("Catalogue has no endpoints array.")
            self._endpoints = [item for item in endpoints if isinstance(item, dict)]
            self.loaded = True
            logger.info("Loaded %d NinjaOne API operations from local catalogue.", len(self._endpoints))
            return True
        except FileNotFoundError:
            self._endpoints = []
            self.loaded = False
            logger.info("Local NinjaOne API catalogue not found at %s; using MCP discovery.", self.path)
            return False
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._endpoints = []
            self.loaded = False
            logger.warning("Could not load local NinjaOne API catalogue: %s", exc)
            return False

    @property
    def endpoint_count(self) -> int:
        return len(self._endpoints)

    def search(self, query: str, limit: int = 8, method: str | None = None) -> list[dict[str, Any]]:
        """Rank documented endpoints by query words, path, tags, and field names."""
        words = {word for word in re.findall(r"[a-z0-9]+", (query or "").casefold()) if len(word) > 1}
        if not words or not self._endpoints:
            return []
        wanted_method = method.upper() if method else None
        ranked: list[tuple[int, dict[str, Any]]] = []
        for endpoint in self._endpoints:
            if wanted_method and endpoint.get("method") != wanted_method:
                continue
            path = str(endpoint.get("path") or "").casefold()
            summary = str(endpoint.get("summary") or "").casefold()
            description = str(endpoint.get("description") or "").casefold()
            tags = " ".join(endpoint.get("tags") or []).casefold()
            operation_id = str(endpoint.get("operation_id") or "").casefold()
            field_names = " ".join(str(field.get("name") or "") for field in endpoint.get("response_fields") or []).casefold()
            param_names = " ".join(str(field.get("name") or "") for field in endpoint.get("parameters") or []).casefold()
            score = 0
            for word in words:
                if word in path:
                    score += 8
                if word in summary:
                    score += 7
                if word in tags:
                    score += 5
                if word in operation_id:
                    score += 4
                if word in field_names:
                    score += 5
                if word in param_names:
                    score += 3
                if word in description:
                    score += 1
            if score:
                ranked.append((score, endpoint))
        ranked.sort(key=lambda item: (-item[0], item[1].get("method", ""), item[1].get("path", "")))
        return [self._public_summary(item) for _, item in ranked[:max(1, limit)]]

    @staticmethod
    def _public_summary(endpoint: dict[str, Any]) -> dict[str, Any]:
        """Return compact, model-friendly endpoint metadata, excluding unsafe field names."""
        safe_fields = []
        for field in endpoint.get("response_fields") or []:
            name = str(field.get("name") or "")
            if any(part in name.casefold() for part in _SENSITIVE_FIELD_PARTS):
                continue
            safe_fields.append({
                "name": name,
                "type": field.get("type"),
                "description": field.get("description", ""),
            })
        return {
            "method": endpoint.get("method"),
            "path": endpoint.get("path"),
            "operation_id": endpoint.get("operation_id"),
            "summary": endpoint.get("summary"),
            "description": endpoint.get("description"),
            "tags": endpoint.get("tags") or [],
            "parameters": endpoint.get("parameters") or [],
            "response_fields": safe_fields[:80],
        }
