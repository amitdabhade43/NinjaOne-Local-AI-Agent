"""Conservative NinjaOne tool-risk classification without substring false positives."""
from __future__ import annotations

import re
from typing import Any


_READ_PREFIXES = ("list_", "get_", "find_", "search_", "describe_", "discover_", "inspect_")
_DESTRUCTIVE_ACTIONS = {
    "delete", "remove", "wipe", "uninstall", "retire", "destroy", "purge", "erase",
}
_WRITE_ACTIONS = {
    "create", "update", "modify", "patch", "put", "post", "set", "run", "execute",
    "reboot", "restart", "shutdown", "install", "action", "assign", "enable", "disable",
}


def classify_tool(tool: dict[str, Any], arguments: dict[str, Any] | None = None) -> str:
    """Return read, write, or destructive. Unknown operations are never assumed safe."""
    args = arguments or {}
    method = str(args.get("method") or args.get("http_method") or args.get("httpMethod") or "").upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        return "read"
    if method == "DELETE":
        return "destructive"
    if method in ("POST", "PUT", "PATCH"):
        return "write"

    name = str(tool.get("name", "")).strip().casefold()
    if name == "memory_list":
        return "read"
    if tool.get("destructive_hint") is True:
        return "destructive"
    if tool.get("read_only_hint") is True:
        return "read"

    # Match whole action tokens in the tool name only. Descriptive prose can
    # mention capabilities such as "remove" while describing a read-only tool.
    tokens = set(re.findall(r"[a-z0-9]+", name))
    if tokens & _DESTRUCTIVE_ACTIONS:
        return "destructive"
    if tokens & _WRITE_ACTIONS:
        return "write"
    if name.startswith(_READ_PREFIXES):
        return "read"
    return "write"


def approval_decision(tool: dict[str, Any], allow_delete: bool,
                      arguments: dict[str, Any] | None = None) -> tuple[bool, str]:
    kind = classify_tool(tool, arguments)
    if kind == "destructive" and not allow_delete:
        return False, "blocked"
    if kind in ("write", "destructive"):
        return False, "approval_required"
    return True, "allowed"
