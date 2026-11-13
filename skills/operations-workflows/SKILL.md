---
name: operations-workflows
description: Coordinate multi-step NinjaOne operations workflows including alert investigation, root-cause analysis, fleet health audits, patch history, patch compliance, security posture, and approval-gated remediation. Use for "investigate", "audit", "fleet health", "root cause", "what is wrong", "patch history", "compliance", "at risk", or cross-resource questions.
---

# Multi-step NinjaOne operations workflows

Use this skill when a question spans multiple NinjaOne resource types or requires an evidence-based investigation. The gateway is the only source of tenant facts.

## Workflow contract

1. **Define scope.** Identify organization, device population, severity/status filters, time window, and requested output. Resolve names to live IDs; never guess IDs.
2. **Discover and inspect.** Search the API catalogue and gateway for candidate GET operations. Inspect each exact operation before execution. Use only parameters and fields present in the inspected schema.
3. **Collect evidence.** Start with the narrowest collection query. Retrieve related records by stable IDs from the returned data, not by display-name guesses.
4. **Handle volume.** Use execute_paginated_endpoint only when both pagination parameters are declared by the endpoint schema. Set a bounded page size and maximum page count. If the endpoint is truncated and cannot be paginated safely, report the result as partial.
5. **Correlate.** Join alerts, activities, device details, patch records, organizations, and jobs by their stable identifiers. Preserve source timestamps and status fields.
6. **Classify.** Separate observed facts, calculated metrics, and hypotheses. Never label a device compliant, healthy, or fixed when required telemetry is missing.
7. **Recommend.** Present the smallest safe next action and exact targets. Investigation and recommendation are not permission to change anything.
8. **Execute only after approval.** POST/PUT/PATCH require the backend's explicit approval flow. DELETE is prohibited. Never invent or expose approval tokens.
9. **Verify.** After an approved operation, retrieve the resulting job/activity/device state. Distinguish accepted, queued, running, succeeded, failed, and unverified outcomes.

## Alert and incident investigation

- Retrieve alerts using documented status, severity, organization, device, and time filters.
- For each relevant alert, capture alert ID, title, severity, state, first/last occurrence, acknowledgement/resolution timestamps, and associated device/organization IDs when returned.
- Retrieve activity history and the relevant device aspects only when they help explain the alert.
- Build a timeline in timestamp order. Identify repeated events and correlated alerts on the same device.
- State a likely cause only when multiple returned facts support it; label it as a hypothesis and list the supporting evidence.
- Do not equate an alert with a ticket or assume acknowledgement means resolution.

## Fleet health and inventory audits

- Define the fleet denominator before calculating percentages. State whether disabled, decommissioned, or excluded devices are omitted and why.
- Inspect documented device collection filters before choosing a population. Do not fetch every device aspect for every device unless the user asks for a full audit and the request can be bounded.
- Evaluate independent signals separately: last check-in, online status, monitoring status, agent health, disk capacity, OS state, antivirus, backup, and patch status.
- Use explicit thresholds from the user or report them as assumptions. Never silently invent a threshold.
- Classify each metric as compliant, non-compliant, or unknown. Missing telemetry is unknown, not compliant.
- Group findings by organization and severity; include a concise exception list with device IDs.

## Patch history and patch compliance

- Inspect OS patch and third-party patch operations separately; their status models may differ.
- Distinguish available, missing, approved, declined, downloading, installing, installed, failed, and pending reboot states when the API returns them.
- Include KB or patch identifier, device identity, severity, scan/install timestamps, and error details when available.
- For fleet totals, paginate safely and state the page limit, total returned, and any incomplete coverage.
- A scan, approval, install, decline, or reboot is a state-changing operation. Enumerate targets and expected impact before asking for approval.
- A queued job is not proof of a successful patch. Verify job status and device patch state after execution.

## Report format

For a multi-resource investigation or audit, return:

1. **Executive summary** — the main result in plain language.
2. **Scope** — organizations, device population, filters, time range, and thresholds.
3. **Findings** — metrics with numerator, denominator, and timestamp.
4. **Exceptions** — affected device/resource, stable ID, severity/status, and evidence.
5. **Unknown or incomplete data** — missing telemetry, failed endpoints, truncation, or page limits.
6. **Recommended next steps** — read-only checks first, then exact approval-gated actions if needed.

Keep raw JSON out of the normal answer. Do not claim completeness unless all required queries completed and pagination was verified.
