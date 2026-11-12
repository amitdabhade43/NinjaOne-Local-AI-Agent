---
name: alerts-activities-tickets
description: Investigate NinjaOne alerts, activities, incidents, ticket records, unresolved issues, severity, status, and timelines. Use for "problems", "issues", "critical", "unresolved", "what happened", or ticket questions.
---

# Alerts, activities, and tickets

- Clarify or infer a bounded time window only when the request provides one; otherwise state the default time scope used.
- Distinguish alert state, severity, acknowledgement, resolution, and ticket state.
- Query the alert/activity/ticket resource that matches the user's intent; do not assume every alert is a ticket.
- Link records to devices and organizations using IDs from live responses.
- For "why did this happen?", gather event/activity history and relevant device context before offering a likely explanation.
- Label hypotheses separately from facts.
- Creating or updating tickets is a write and requires approval.