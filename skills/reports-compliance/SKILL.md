---
name: reports-compliance
description: Build NinjaOne reports for fleet health, device inventory, patch compliance, antivirus, software, operating systems, disks, logged-on users, and organization summaries.
---

# Reporting and compliance

- Translate the request into a report definition: population, metrics, filters, time window, grouping, and output.
- Prefer documented report endpoints when they directly provide the requested data; otherwise combine supported GET endpoints.
- Check pagination and response truncation before calculating totals or percentages.
- State denominator, excluded records, time range, and missing data for every metric.
- Never infer compliance from missing telemetry. Mark it unknown.
- For large results, summarize by organization/severity and include representative exceptions.