---
name: ninjaone-api-discovery
description: Discover, inspect, and execute NinjaOne API v2 operations from fuzzy natural-language requests. Use when the correct API endpoint, parameters, filters, or multi-step API workflow is not already known.
---

# NinjaOne API discovery

## Workflow

1. Translate the request into resources, actions, filters, scope, and desired output.
2. If the gateway exposes `discover_endpoints`, call it with a concise natural-language intent. If it exposes the legacy `find_endpoint`, use that instead.
3. Compare candidate summaries. Do not select solely by a similar endpoint name; use summary, tags, description, and operation ID.
4. If available, call `inspect_endpoint` for the selected exact endpoint key; with the legacy gateway, use `describe_endpoint`.
5. Read required path/query parameters, request-body schema, enums, and response shape.
6. Resolve IDs from live API responses. Never fabricate an ID.
7. With the schema-driven gateway, call `execute_endpoint` with separate `path_params`, `query`, and `body` objects. With the legacy gateway, use `call` after describing the endpoint. Use the raw-request tool only when the exact method/path has been matched to the OpenAPI catalogue.
8. For GET results, inspect `complete` and `truncated`. If incomplete, use documented pagination or explicitly state the limitation.
9. For multi-resource questions, execute each necessary read step and join records using stable IDs. Explain missing joins rather than guessing.

## Tool rules

- `discover_endpoints` and `inspect_endpoint` are discovery/read operations.
- `execute_paginated_endpoint` is GET-only. Supply exact OpenAPI-declared page and page-size parameter names and a result_path when the array cannot be identified unambiguously.
- Never send an approval token. The trusted backend injects it after human approval.
- POST/PUT/PATCH must be presented for approval. DELETE is blocked.
- Never retry a write after a timeout without checking whether it completed.

## Fuzzy language

Map user language to API intent. Examples: "machines" may mean devices; "sites" may mean locations; "people" may mean users or technicians; "problems" may mean alerts, tickets, or activities. If more than one interpretation materially changes the result, ask one concise clarification.