from typing import Any


def classify_tool(tool: dict[str, Any]) -> str:
    """Return read, write, or destructive. Unknown tools are treated as writes."""
    if tool.get("destructive_hint") is True:
        return "destructive"
    if tool.get("read_only_hint") is True:
        return "read"
    name = str(tool.get("name", "")).lower()
    description = str(tool.get("description", "")).lower()
    text = f"{name} {description}"
    if any(word in text for word in ("delete", "remove", "wipe", "uninstall", "retire", "destroy")):
        return "destructive"
    if any(word in text for word in ("create", "update", "modify", "patch", "put", "post", "set_", "run_script",
                                     "execute", "reboot", "restart", "shutdown", "install", "action")):
        return "write"
    # Tool names without trustworthy read-only metadata are not assumed safe.
    return "read" if name.startswith(("list_", "get_", "find_", "search_", "describe_", "discover_")) else "write"


def approval_decision(tool: dict[str, Any], allow_delete: bool) -> tuple[bool, str]:
    kind = classify_tool(tool)
    if kind == "destructive" and not allow_delete:
        return False, "blocked"
    if kind in ("write", "destructive"):
        return False, "approval_required"
    return True, "allowed"
