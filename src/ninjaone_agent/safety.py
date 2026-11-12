from typing import Any


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

    name = str(tool.get("name", "")).lower()
    if name == "memory_list":
        return "read"
    if tool.get("destructive_hint") is True:
        return "destructive"
    if tool.get("read_only_hint") is True:
        return "read"
    description = str(tool.get("description", "")).lower()
    text = f"{name} {description}"
    if any(word in text for word in ("delete", "remove", "wipe", "uninstall", "retire", "destroy")):
        return "destructive"
    if any(word in text for word in ("create", "update", "modify", "patch", "put", "post", "set_", "run_script",
                                     "execute", "reboot", "restart", "shutdown", "install", "action")):
        return "write"
    return "read" if name.startswith(("list_", "get_", "find_", "search_", "describe_", "discover_", "inspect_")) else "write"


def approval_decision(tool: dict[str, Any], allow_delete: bool,
                      arguments: dict[str, Any] | None = None) -> tuple[bool, str]:
    kind = classify_tool(tool, arguments)
    if kind == "destructive" and not allow_delete:
        return False, "blocked"
    if kind in ("write", "destructive"):
        return False, "approval_required"
    return True, "allowed"
