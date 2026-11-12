---
name: devices-inventory
description: Search NinjaOne endpoints, laptops, servers, workstations, hardware, operating systems, software, users, disks, services, network and device details. Use for device inventory, ownership, health, or status questions.
---

# Devices and inventory

- Search by hostname, serial number, system name, user, organization, IP, or device type using documented filters.
- Resolve a device to its unique NinjaOne ID before requesting details or performing an operation.
- Separate device online status, last check-in, monitoring status, and agent/service status; they are not equivalent.
- For "not checking in", request or calculate the time window from last-contact/last-seen fields and state the threshold.
- For disk, memory, CPU, OS, software, and hardware questions, inspect the relevant device aspect endpoint and its response schema.
- Join related data by device ID. Avoid matching only on display names.
- Report the data timestamp and whether the result set is complete.