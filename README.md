# NinjaOne Local AI Agent

A self-hosted, browser-based NinjaOne assistant. It uses locally hosted models exposed through Microsoft Foundry Local and connects to a **separate** NinjaOne MCP gateway. No cloud LLM API is required.

## Current implementation

- Dynamic Foundry model discovery; no model ID is hard-coded.
- Browser chat UI and FastAPI endpoints.
- Dynamic MCP tool discovery through Streamable HTTP.
- Model-driven tool selection with native OpenAI-style function calling where supported.
- JSON tool-call fallback when the configured endpoint rejects native function-calling parameters.
- Bounded multi-step tool execution and conversation history supplied by the browser.
- Live results are passed back to the model; the agent is instructed not to invent NinjaOne facts.
- Read-only operations can run automatically. Write operations require explicit approval by default.
- Destructive operations are blocked by default.
- Local-only bind address by default.

> **Compatibility note:** Foundry's OpenAI-compatible API does not mean every model supports native tool calling equally. The fallback improves compatibility, but a model that cannot reliably follow tool schemas/JSON may still need a better model or a model-specific prompt. The agent validates tool names and argument object shape; the MCP server remains responsible for endpoint-level schema validation and authorization.

## Architecture

```text
Browser
   |
   v
FastAPI agent (127.0.0.1:8080)
   |                         |
   v                         v
Foundry Local            NinjaOne MCP gateway (MCP7)
local model              separate process/repository
                              |
                              v
                         NinjaOne API
```

This repository contains the agent only. It does not copy, replace, or modify the MCP gateway. Configure the MCP endpoint in `.env`.

## Requirements

- Windows 10/11 or another OS supported by Python.
- Python 3.11 or newer (64-bit on Windows).
- Microsoft Foundry Local running with at least one model loaded.
- NinjaOne MCP gateway running and reachable through Streamable HTTP.
- Network access from the agent host to the configured local services.

## Windows PowerShell installation

Run from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

If PowerShell blocks activation, use the venv interpreter directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Configure `.env`

1. Set `FOUNDRY_BASE_URL` to the current OpenAI-compatible Foundry endpoint, including `/v1` if shown by Foundry. The port may change between sessions/model loads.
2. Leave `FOUNDRY_MODEL` blank to select the first model returned by the endpoint, or set an exact ID shown by `/api/models`.
3. Set `NINJAONE_MCP_URL` to the MCP7 Streamable HTTP URL. The example `http://127.0.0.1:8000/mcp` is only a placeholder; use the actual gateway URL and path.
4. Keep `HOST=127.0.0.1` for local-only access. Do not expose the agent to the LAN until authentication and access controls have been added.

Never copy NinjaOne credentials into this repository. Keep them in the MCP gateway's own local environment. Never commit `.env`, API tokens, device exports, tenant data, or logs.

### Start the agent

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m ninjaone_agent.main
```

Open **http://127.0.0.1:8080** in your browser. API documentation is at **http://127.0.0.1:8080/docs**.

## Verify services

In a second PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/api/models
Invoke-RestMethod http://127.0.0.1:8080/api/tools
```

Send a basic agent request:

```powershell
$body = @{ message = "List the NinjaOne organizations and show their names." } | ConvertTo-Json -Depth 10
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/chat -Method Post -ContentType "application/json" -Body $body
```

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Browser chat UI |
| GET | `/health` | Agent process health |
| GET | `/api/models` | Dynamic Foundry model discovery |
| GET | `/api/tools` | Dynamic MCP tool discovery |
| POST | `/api/chat` | Agent request with optional model and history |
| POST | `/api/approvals/{approval_id}` | Approve or reject a pending write operation |

## Safety behavior

- MCP tools marked read-only by server annotations may run automatically.
- Dynamic gateway calls that specify HTTP `GET` are treated as read-only; `POST`, `PUT`, and `PATCH` require approval.
- Unknown tools default to requiring approval unless their names clearly indicate a read operation.
- Delete-like operations and HTTP `DELETE` are blocked unless `ALLOW_DELETE_OPERATIONS=true`. Enabling this setting is not recommended for initial testing.
- Approval requests are held in process memory and are lost if the agent restarts.
- The MCP gateway and NinjaOne API must still enforce their own credentials, permissions, and endpoint-level authorization.

This is a local development/demo baseline, not a hardened multi-user production service. It currently has no user authentication, durable approval store, role-based access control, or persistent server-side conversation database. Do not bind it to `0.0.0.0` or expose it to untrusted networks.

## Tests

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest -q
```

Tests cover API health, tool mapping, JSON fallback parsing, and safety classification. They do not require Foundry or a live NinjaOne tenant.

## Troubleshooting

- **Foundry connection refused:** confirm a model is loaded and update `FOUNDRY_BASE_URL` to the current endpoint.
- **No models returned:** check the endpoint path and verify Foundry's OpenAI-compatible models route.
- **MCP connection error:** verify the MCP7 process, URL, transport type (Streamable HTTP), and `/mcp` path.
- **MCP tools list but calls fail:** inspect the gateway logs and confirm the gateway's NinjaOne credentials/scopes. This agent does not bypass gateway authorization.
- **Model does not call tools:** try another loaded model. The JSON fallback is best-effort and does not guarantee tool-use quality across every model.

## Roadmap

- Model-by-model compatibility and evaluation harness.
- Better pagination and multi-step planning for gateway-specific tool patterns.
- Durable conversation state and audit events with secret/data redaction.
- Optional model-specific fine-tuning after collecting reviewed, synthetic or sanitized examples.
- LAN deployment only after authentication, authorization, and secure secret handling are implemented.

## Contributing

Use synthetic examples. Redact organization names, device identifiers, user details, tokens, tenant data, and other sensitive information from issues and pull requests.
