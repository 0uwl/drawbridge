# Web API

**Entry point:** `drawbridge/main.py` — creates the Flask app via factory function
`create_app()`. Gunicorn is used as the WSGI server inside the container (see
[deployment.md](deployment.md)).

**Key endpoints:**

All `/api/*` paths below are versioned — `API_PREFIX` in `main.py` is
`/api/v1`, so e.g. `/api/devices` in this table is actually
`/api/v1/devices`. `/files/*` paths are not versioned.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/provision-request` | Called by the ZTP script's phone-home step on boot; approves or denies provisioning |
| GET | `/api/v1/devices` | List devices currently pending provisioning |
| POST | `/api/v1/devices` | Register a new device (serial + optional metadata) |
| DELETE | `/api/v1/devices/<serial>` | Remove a device from the allowlist |
| GET | `/api/v1/devices/<serial>` | Get a pending device's status (not history — see `/api/v1/log`) |
| GET | `/api/v1/devices/sessions` | List all active provisioning sessions |
| GET | `/api/v1/devices/sessions/<serial>` | Get the active provisioning session for a device |
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
| GET | `/files/scripts/<filename>` | Download a ZTP script — unauthenticated, served to devices during ZTP (Kea's Option 67 boot-file-name points here, see [kea.md](kea.md)) |
| DELETE | `/files/scripts/<filename>` | Delete a ZTP script (auth required) |
| PUT | `/api/v1/provision-complete` | Device reports provisioning outcome; deletes the `ProvisioningSession` row, writes a `ProvisioningLog` row. The `devices` allowlist row is untouched. POST is also accepted for testing/debugging. |
| GET | `/api/v1/log` | List provisioning log entries (time, image, config file, outcome) within the retention window |
| POST | `/api/v1/auth/login` | Local username/password login, starts session |
| POST | `/api/v1/auth/logout` | Ends the current session |
| GET | `/api/v1/auth/me` | Current authenticated operator (id, username, role, auth_source) |
| POST | `/api/v1/auth/claim` | First-time password creation for an admin-created local account (`username` + `password`, unauthenticated) |
| POST | `/api/v1/auth/change-password` | Change the current user's own password (`current_password` + `new_password`, any role) |
| POST | `/api/v1/auth/reset-password` | Completes a forced password reset (`username` + `current_password` + `new_password`, unauthenticated). Only succeeds when `must_reset_password` is set — see [authentication.md](authentication.md) |
| GET | `/api/v1/users` | List operator accounts (admin only) |
| POST | `/api/v1/users` | Create an operator account with no password set yet (admin only) |
| PUT | `/api/v1/users/<id>` | Change an operator's role (admin only) |
| DELETE | `/api/v1/users/<id>` | Remove an operator account, no password confirmation required (admin only) |
| GET | `/api/v1/settings/log-retention` | Current log retention setting (days, or indefinite) |
| PUT | `/api/v1/settings/log-retention` | Update log retention setting (admin only) |

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

## `/api/v1/provision-request` contract

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
