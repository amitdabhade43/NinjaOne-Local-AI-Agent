"""Backend-only signer for the NinjaOne gateway's exact-operation approvals."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import dotenv_values


class ApprovalSigningError(RuntimeError):
    """The trusted backend cannot safely mint an operation approval."""


def canonical_body_digest(body: Any) -> str:
    raw = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def resolve_path(template: str, path_params: dict[str, Any] | None = None) -> str:
    """Resolve OpenAPI {parameters} exactly as the gateway does."""
    import re

    values = path_params or {}
    names = re.findall(r"\{([^}]+)\}", template)
    missing = [name for name in names if name not in values or values[name] is None]
    extra = sorted(set(values) - set(names))
    if missing:
        raise ApprovalSigningError(
            "Cannot approve operation; missing path parameter(s): " + ", ".join(missing)
        )
    if extra:
        raise ApprovalSigningError(
            "Cannot approve operation; unexpected path parameter(s): " + ", ".join(extra)
        )

    def replace(match: re.Match[str]) -> str:
        value = values[match.group(1)]
        if isinstance(value, (dict, list)) or str(value) == "":
            raise ApprovalSigningError(
                f"Path parameter {match.group(1)!r} must be a non-empty scalar."
            )
        return quote(str(value), safe="")

    return re.sub(r"\{([^}]+)\}", replace, template)


def _secret_from_environment(env_file: Path | str | None = None) -> str:
    secret = os.getenv("NINJA_MCP_APPROVAL_SECRET", "")
    if secret:
        return secret
    if env_file:
        values = dotenv_values(Path(env_file))
        secret = str(values.get("NINJA_MCP_APPROVAL_SECRET") or "")
    if not secret:
        raise ApprovalSigningError(
            "Write approval is unavailable: NINJA_MCP_APPROVAL_SECRET is not configured."
        )
    return secret


def mint_approval_token(
    *,
    method: str,
    path: str,
    body: Any,
    env_file: Path | str | None = None,
    lifetime_seconds: int = 60,
) -> str:
    """Mint a short-lived, single-use token after the human approval decision."""
    method = method.upper()
    if method not in {"POST", "PUT", "PATCH"}:
        raise ApprovalSigningError("Only POST, PUT, and PATCH operations require approval tokens.")
    if not path.startswith("/") or "{" in path or "}" in path:
        raise ApprovalSigningError("Approval path must be a fully resolved relative API path.")
    if not 1 <= lifetime_seconds <= 300:
        raise ApprovalSigningError("Approval token lifetime must be between 1 and 300 seconds.")

    secret = _secret_from_environment(env_file)
    payload = {
        "method": method,
        "path": path,
        "body_sha256": canonical_body_digest(body),
        "exp": int(time.time()) + lifetime_seconds,
        "jti": str(uuid.uuid4()),
    }
    encoded = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + _b64url(signature)
