---
name: organizations-locations
description: Find and manage NinjaOne organizations, locations, groups, organization settings, and tenant scope. Use for customer, site, branch, organization, or group questions.
---

# Organizations and locations

- Resolve organization and location names through live discovery and read endpoints.
- For cross-organization requests, establish the scope first and retain organization IDs in subsequent calls.
- Distinguish organizations, locations, and device groups; do not treat them as interchangeable.
- When a request says "all customers" or "company-wide", query the appropriate tenant-wide endpoint and check pagination.
- For organization configuration changes, inspect the exact request schema and present a before/after proposal for approval.
- Summaries should include organization/location names and IDs, plus counts only when calculated from complete results.