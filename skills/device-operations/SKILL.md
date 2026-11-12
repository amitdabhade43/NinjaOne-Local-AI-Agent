---
name: device-operations
description: Perform approved NinjaOne device actions such as reboot, shutdown, service control, agent actions, maintenance, and remediation. Use when the user asks to fix, restart, repair, or change a device.
---

# Device operations

- Convert "fix it" into an investigation first. Identify the fault, target, and an appropriate documented remediation.
- Confirm exact device identity and organization. If multiple devices match, ask for clarification.
- Inspect operation schema, required parameters, supported platform, and side effects.
- Present a clear approval proposal listing each target and exact action.
- Never broaden a single-device request to a fleet action.
- Do not perform destructive or irreversible actions. DELETE is unavailable.
- After approval, execute once and verify using job/activity/device state. Report queued versus completed distinctly.