---
name: users-technicians
description: Find NinjaOne users, technicians, roles, device assignments, logged-on users, and user-to-device relationships. Use for people, ownership, access, or assignment questions.
---

# Users and technicians

- Determine whether the user means an end-user account, technician account, logged-on account, or device owner.
- Search by exact email/name where available; handle duplicate names by showing distinguishing attributes.
- Resolve user and device IDs before querying relationships.
- Do not expose secrets, authentication tokens, MFA seeds, or unnecessary personal data.
- For role/access changes, inspect current assignments and target role, then require approval.
- Return only fields needed to answer the request.