# NinjaOne Local AI Agent

A self-hosted, browser-based NinjaOne operations agent. It uses locally hosted models through Microsoft Foundry Local and launches the separate NinjaOne API Gateway MCP as a local STDIO subprocess. No cloud LLM API is required.

## Design

- **Foundry Local** provides the model. The agent discovers the active Foundry Local endpoint and loaded models.
- **FastAPI** hosts the browser UI and chat/approval APIs on loopback by default.
- **NinjaOne API Gateway MCP** provides schema-driven discovery and execution from the NinjaOne API v2 OpenAPI catalogue.
- **Markdown skills** provide domain-specific operating procedures and are loaded progressively based on the user request.
- **Backend approval signing** creates a short-lived HMAC token only after a human approves a specific POST/PUT/PATCH operation. The model never receives the approval secret or token.

~~~text
Browser
  |
  v
FastAPI agent ---- Foundry Local (local model)
  |
  | one STDIO MCP session per agent turn
  v
NinjaOne API Gateway MCP
  |
  v
NinjaOne Public API v2
~~~

## Current capabilities

- Dynamic Foundry model discovery; no model ID is hard-coded.
- Native OpenAI-compatible tool calls, Qwen <tool_call> markup, and JSON tool-plan fallback.
- Dynamic MCP tool discovery and bounded multi-step execution.
- One MCP subprocess/session is reused across discovery, inspection, and execution in a single agent turn to avoid repeated process startup.
- Master agent instructions plus focused domain skills with progressive disclosure.
- Live results are returned to the model; the agent is instructed not to invent tenant facts.
- GET operations can run automatically.
- POST/PUT/PATCH operations require explicit approval. When the MCP tool schema exposes the gateway HMAC approval_token, the backend signs the exact method, resolved path, and request-body hash after approval; legacy tools without that parameter remain protected by the agent approval gate and cannot receive an injected unknown argument.
- DELETE is blocked by the gateway and is not enabled by this agent.
- The agent supports the current schema-driven gateway tools and includes prompt guidance for the legacy endpoint-discovery tool names.
- Simple, unfiltered inventory list/count questions use a deterministic read-only fast path when the connected gateway exposes the legacy `find_endpoint`, `describe_endpoint`, and `call` tools. It discovers and inspects the documented endpoint, verifies it is GET, and calls it directly without model planning/finalization. Filtered, investigative, write, and unsupported requests stay on the normal agent workflow.
- The browser chat shows each completed MCP step, its status, and returned result in a collapsible activity timeline. Activity is displayed when the response completes; it is not token-by-token/live streaming.

## Skills

agents/ninjaone-expert.md contains the master operating policy. Domain skills live under skills/*/SKILL.md and use Agent Skills-style YAML frontmatter.

Current skill areas:

- API discovery and schema inspection
- Organizations, locations and groups
- Devices and inventory
- Users, technicians and assignments
- Alerts, activities and tickets
- Policies and configuration
- Patch management
- Automation and scripts
- Device operations
- Reports and compliance
- Backup, IT asset management, knowledge and access

The loader always includes API-discovery guidance and loads only the best matching domain skills for each request. It does not load every skill on every turn.

## Requirements

- Windows 10/11 or another OS supported by Python.
- Python 3.11 or newer (64-bit on Windows).
- Microsoft Foundry Local running with at least one model loaded.
- NinjaOne API Gateway MCP repository available locally.
- A configured NinjaOne API client with the necessary tenant scopes.

Expected sibling-folder layout:

~~~text
C:\project\
  NinjaOne-Local-AI-Agent\
  NinjaRMM-API-Gateway-MCP\
~~~

## Windows PowerShell installation

Run from C:\project\NinjaOne-Local-AI-Agent:

~~~powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
~~~

If the virtual environment does not exist yet:

~~~powershell
C:\Program Files\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

### Configure the agent

The Foundry endpoint is discovered from the Foundry CLI. Set FOUNDRY_BASE_URL only if you need a custom/fallback endpoint. Leave FOUNDRY_MODEL blank to select the first loaded model or set an exact model ID returned by /api/models.

Keep:

~~~dotenv
HOST=127.0.0.1
PORT=8080
APPROVAL_REQUIRED_FOR_WRITES=true
ALLOW_DELETE_OPERATIONS=false
~~~

The gateway path defaults assume the two repositories are sibling folders under C:\project. If yours differ, uncomment the NINJAONE_GATEWAY_DIR, NINJAONE_GATEWAY_PYTHON, and NINJAONE_GATEWAY_ENV_FILE overrides in .env.example.

### Configure the gateway

Keep NinjaOne API credentials and NINJA_MCP_APPROVAL_SECRET only in:

C:\project\NinjaRMM-API-Gateway-MCP\.env

When the active MCP tool schema exposes the gateway HMAC approval_token, the approval secret must be the same secret configured for the gateway's HMAC verifier. The agent reads it from the gateway environment file only inside the trusted backend when an approval is accepted. Legacy tool schemas without an approval_token parameter use the agent-side human approval gate and do not receive an extra argument. Never put the secret in a prompt, model context, browser JavaScript, chat history, or Git.

The gateway must have its own dependencies installed in its own virtual environment. The agent starts it with:

~~~powershell
C:\project\NinjaRMM-API-Gateway-MCP\.venv\Scripts\python.exe -m ninja_mcp
~~~

## Start

From the agent repository root:

~~~powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m ninjaone_agent.main
~~~

Open http://127.0.0.1:8080. API documentation is available at http://127.0.0.1:8080/docs.

## Verify

In a second PowerShell window:

~~~powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/api/models
Invoke-RestMethod http://127.0.0.1:8080/api/tools
~~~

Basic live-data test:

~~~powershell
$body = @{ message = "List all NinjaOne organizations and show their names and IDs." } | ConvertTo-Json -Depth 10
$response = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/chat" -Method Post -ContentType "application/json" -Body $body
$response | ConvertTo-Json -Depth 20
~~~

The response should include completed tool steps. If there are no tool steps, do not treat the answer as verified live tenant data.

## Approval flow

1. The model discovers and inspects the requested API endpoint.
2. The backend verifies the HTTP method and resolves path parameters from the inspected OpenAPI path.
3. GET requests execute automatically.
4. POST/PUT/PATCH requests return a pending approval with the exact tool arguments and risk.
5. After the user approves, the backend creates a short-lived HMAC token bound to the exact method, resolved path and canonical body hash.
6. The token is passed to the gateway internally; it is never exposed to the model.
7. The gateway validates the signature, expiry, operation binding and one-time token ID before execution.

DELETE is not supported. If the approval secret is missing or the operation cannot be mapped safely, the agent blocks the write rather than bypassing the gateway.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | / | Browser chat UI |
| GET | /health | Agent process health |
| GET | /api/models | Dynamic Foundry model discovery |
| GET | /api/tools | Dynamic MCP tool discovery |
| POST | /api/chat | Agent request with optional model and history |
| POST | /api/approvals/{approval_id} | Approve or reject a pending write |

## Tests

From the repository root:

~~~powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest -v
~~~

The tests are local and do not require Foundry or a live NinjaOne tenant. They cover tool-call parsing, argument validation, skill selection, operation-risk classification, approval-token construction, and MCP session reuse. Live integration must still be checked against your Foundry runtime, gateway environment, NinjaOne API permissions, and a controlled tenant.

## Security limitations

This remains a local development/demo service, not a hardened multi-user production deployment. It has no user authentication, durable approval store, role-based authorization layer, or persistent server-side conversation database. Pending approvals are held in memory and expire when the agent restarts.

Keep HOST=127.0.0.1. Do not expose the service to the LAN or bind to 0.0.0.0 until authentication, CSRF protection, user/role authorization, durable audit logging, and persistent approval handling have been implemented and reviewed.

## Performance notes

The per-turn MCP session reuse removes repeated gateway Python process startup between tool calls. Progressive skill loading limits domain instructions to the most relevant skills. Neither change guarantees a particular model latency: Foundry model generation, thinking-token count, context length, number of tool rounds, and NinjaOne API latency still affect total response time.

## Local latency benchmark

After starting the agent and connecting the gateway, run the read-only management benchmark:

~~~powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe .\scripts\benchmark_agent.py
~~~

To compare loaded Foundry models, run the same questions once per model:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_agent.py --model "qwen3-14b-generic-gpu" --output reports\qwen3-14b.json
~~~

The report records latency, tool count, tool names, statuses, and answer length. It deliberately does not save answer bodies or NinjaOne records. Run only against a tenant where these read-only queries are permitted.

## Reference

The Markdown skill layout and progressive-disclosure pattern are adapted from https://github.com/wshobson/agents. Its Claude/Codex/Cursor/OpenCode adapters and optional external LLM evaluation are not dependencies of this project.
