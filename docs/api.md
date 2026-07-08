# Web API

**Entry point:** `drawbridge/main.py` — creates the Flask app via factory function
`create_app()`. Gunicorn is used as the WSGI server inside the container (see
[deployment.md](deployment.md)).

**Key endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/provision-request` | Called by the ZTP script's phone-home step on boot; approves or denies provisioning |
| GET | `/api/devices` | List devices currently pending provisioning |
| POST | `/api/devices` | Register a new device (serial + optional metadata) |
| DELETE | `/api/devices/<serial>` | Remove a device from the allowlist |
| GET | `/api/devices/<serial>` | Get a pending device's status (not history — see `/api/log`) |
| GET | `/api/devices/sessions` | List all active provisioning sessions |
| GET | `/api/devices/sessions/<serial>` | Get the active provisioning session for a device |
| GET | `/files/images` | List uploaded OS images (auth required) |
| POST | `/files/images` | Upload an OS image (auth required) |
| GET | `/files/images/<filename>` | Download an OS image — unauthenticated, served to devices during ZTP |
| DELETE | `/files/images/<filename>` | Delete an OS image (auth required) |
| GET | `/files/configs` | List uploaded config files (auth required) |
| POST | `/files/configs` | Upload a config file (auth required) |
| GET | `/files/configs/<filename>` | Download a config file — unauthenticated, served to devices during ZTP |
| DELETE | `/files/configs/<filename>` | Delete a config file (auth required) |
| GET | `/files/scripts` | List uploaded ZTP scripts (auth required) |
| POST | `/files/scripts` | Upload a ZTP script (auth required) |
| GET | `/files/scripts/<filename>` | Download a ZTP script — unauthenticated, served to devices during ZTP |
| DELETE | `/files/scripts/<filename>` | Delete a ZTP script (auth required) |
| PUT | `/api/provision-complete` | Device reports provisioning outcome; deletes the `ProvisioningSession` row, writes a `ProvisioningLog` row. The `devices` allowlist row is untouched. POST is also accepted for testing/debugging. |
| GET | `/api/log` | List provisioning log entries (time, image, config file, outcome) within the retention window |
| POST | `/api/auth/login` | Local username/password login, starts session |
| POST | `/api/auth/logout` | Ends the current session |
| GET | `/api/auth/me` | Current authenticated operator (id, username, role, auth_source) |
| POST | `/api/auth/claim` | First-time password creation for an admin-created local account (`username` + `password`, unauthenticated) |
| POST | `/api/auth/change-password` | Change the current user's own password (`current_password` + `new_password`, any role) |
| GET | `/api/users` | List operator accounts (admin only) |
| POST | `/api/users` | Create an operator account with no password set yet (admin only) |
| PUT | `/api/users/<id>` | Change an operator's role (admin only) |
| DELETE | `/api/users/<id>` | Remove an operator account, no password confirmation required (admin only) |
| GET | `/api/settings/log-retention` | Current log retention setting (days, or indefinite) |
| PUT | `/api/settings/log-retention` | Update log retention setting (admin only) |

Every `/api/devices`, `/api/log`, `/api/users`, `/api/settings/*`, and
`GET /files/*` list / `POST /files/*` upload / `DELETE /files/*` delete route
requires an authenticated session — see [authentication.md](authentication.md).
`GET /files/<type>/<filename>` download endpoints are unauthenticated so that
IOS XE devices can fetch images, configs, and scripts during ZTP without
credentials; access is restricted at the network level (provisioning VLAN).
`/api/provision-request` and `/api/provision-complete` are both open routes
called by devices, not operators — gated by the serial lookup itself (and,
at the perimeter, network isolation), not by caller identity. There is no
secret an anonymous, unregistered device could hold, so no auth decorator
applies to either route; see [decisions.md](decisions.md) for the
reasoning and the explicitly-accepted serial-enumeration non-goal.

## `/api/provision-request` contract

Called by the ZTP script (`scripts/ztp-base.py`) as its first action after
fetching the script — a plain `GET` with query-string params, not a JSON
POST body, since IOS XE's `copy` primitive (the only network I/O available
from Guestshell — see [decisions.md](decisions.md), "C9200CX network stack
isolation") can't attach a request body:

```
GET /api/v1/provision-request?serial=FJC2517X0AB&mac=aa:bb:cc:dd:ee:ff
```

`mac` is optional (audit/logging only, not used for lookup — the script
always knows its own real serial via `show version`, so no serial/MAC
fallback matching is needed here). Responses:
- `200 OK` → known serial; payload is the `Device` record (image, config
  file, script to fetch next); a `ProvisioningSession` row is created
- `404 device_not_found` → unknown serial; script exits, does not proceed
- `422 missing_parameter` → `serial` missing from the query string
