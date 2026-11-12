---
name: patch-management
description: Investigate NinjaOne OS and third-party patch status, missing patches, failed installations, patch history, reboot requirements, and approved patch operations.
---

# Patch management

- Separate missing, available, approved, declined, failed, installed, and pending-reboot patch states.
- Discover OS patch and third-party patch endpoints independently when relevant.
- Include device identity, patch KB/name, severity where available, last scan/install time, and error details.
- For fleet-wide questions, paginate and group by organization and device.
- A patch scan, approval, installation, or reboot is an operation: inspect schema, enumerate targets, present scope, and require approval.
- Verify results after execution; do not report "patched" based only on a queued job.