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

## Cross-category people search and access changes

- When the user says "all users", search every relevant people category exposed by the connected API, including console users/technicians, end users, and contacts where documented. Do not stop at the first endpoint named users.
- For a name search, retrieve all relevant pages using only documented pagination, then filter first name, last name, and display name case-insensitively. Return category and distinguishing fields so duplicate names are not merged incorrectly.
- Use the API's userType or equivalent discriminator when present. Preserve the difference between a technician, end user, contact, and logged-on user.
- If the request is about a technician group, inspect the API catalogue for the actual supported concept (technician role, role membership, organization permissions, or another group resource). Do not assume device groups are technician groups.
- For user-to-device access, distinguish accessible devices from assigned/owned devices. Inspect existing access before proposing additions or removals. Resolve every device ID and verify it belongs to the intended scope.
- For any user, role, group, or access mutation, inspect the exact endpoint schema and current state first. Confirm the exact person and target resource; ask the user to choose if multiple candidates remain.
- Do not infer that adding a person to a group, assigning a policy, granting device access, changing ownership, and changing a technician role are equivalent operations. Explain the relevant distinction and ask which effect is intended when the request does not establish it.
- Prepare one explicit change summary and require human approval before POST/PUT/PATCH. Never execute DELETE.
