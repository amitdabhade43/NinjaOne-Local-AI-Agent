# NinjaOne Local AI Agent

A self-hosted agent foundation for NinjaOne using locally hosted models exposed through Microsoft Foundry Local and a separate NinjaOne MCP gateway.

> **Status: Stage 1 foundation.** This initial version includes Foundry model discovery, a health endpoint, and basic single-turn chat. NinjaOne MCP execution, approvals, multi-step planning, memory, and browser UI are not implemented yet.

## Goals

- Run locally without cloud LLM APIs.
- Discover model IDs from the configured Foundry endpoint instead of hardcoding a model name.
- Keep model integration separate from agent orchestration.
- Treat model capabilities as variable: an OpenAI-compatible endpoint does not guarantee reliable native tool calling or structured output from every model.
- Ground NinjaOne answers in actual MCP results; never fabricate device or organization data.
- Require explicit approval for supported write operations and block destructive operations by default.
- Keep tenant data, credentials, and local configuration out of source control.

## Requirements

- Python 3.11+
- Microsoft Foundry Local running with at least one model loaded
- The current OpenAI-compatible Foundry API endpoint (including `/v1` where applicable)

## Windows PowerShell setup

From the repository root:

```powershell
py -3.11 -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Set `FOUNDRY_BASE_URL` to the endpoint shown by your current Foundry session. The port may change between sessions or model loads. `FOUNDRY_MODEL` is optional; leave it blank to select the first model returned by the endpoint, or set an exact model ID shown by `/api/models`.

Start the API:

```powershell
$env:PYTHONPATH = "src"
python -m ninjaone_agent.main
```

The default bind address is `127.0.0.1:8080` (local machine only).

## Verify

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/api/models
$body = @{ message = "Reply with a short greeting" } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/chat -Method Post -ContentType "application/json" -Body $body
```

Interactive API documentation: `http://127.0.0.1:8080/docs`.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Process health |
| GET | `/api/models` | Discover models exposed by Foundry |
| POST | `/api/chat` | Basic single-turn chat; optional exact model ID |

## Configuration

See `.env.example`. Never commit `.env`, API tokens, NinjaOne credentials, device exports, request logs, or real tenant data. Example values are placeholders only.

## Architecture boundary

This repository contains the **agent**. The NinjaOne MCP gateway remains a separate service/repository and is configured through `NINJAONE_MCP_URL`. MCP tool discovery and execution will be added in a later stage; this initial version does not yet perform NinjaOne operations.

## Roadmap

1. **Foundation (current):** Foundry endpoint configuration, model discovery, health API, and basic chat.
2. **Model compatibility:** capability probes, structured-output validation, and safe fallback paths for models with weak/no native tool calling.
3. **NinjaOne MCP integration:** discover tools dynamically, inspect schemas before calls, and connect to the separate MCP gateway.
4. **Agent workflow:** planning, pagination, multi-step execution, retries, result validation, conversation context, and grounded responses.
5. **Safety and approvals:** read-only defaults, explicit approval for supported writes, deny rules for destructive operations, and redacted audit events.
6. **UI and evaluation:** browser chat, regression tests, model-by-model evaluation, and optional model-specific fine-tuning experiments.

## Contributing

Issues and pull requests are welcome. Use synthetic examples and redact organization names, device identifiers, user details, tokens, and other tenant information from reports.
