# Web API

**Entry point:** `drawbridge/main.py` — creates the Flask app via factory function
`create_app()`. Gunicorn is used as the WSGI server inside the container (see
[deployment.md](deployment.md)).

**Key endpoints:**

All `/api/*` paths below are versioned — `API_PREFIX` in `main.py` is
`/api/v1`, so e.g. `/api/devices` in this table is actually
`/api/v1/devices`. `/files/*` paths are not versioned.

The ZTP script itself is not one of these routes — it's a single fixed
file, bind-mounted into the separate `drawbridge-bootstrap` container and
served over plain HTTP on `:8090`, not through this Flask app at all. See
[deployment.md](deployment.md) ("Container") and [decisions.md](decisions.md)
("HTTPS cert trust on C9200CX").

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/provision-request` | Called by the ZTP script's phone-home step on boot; approves or denies provisioning |
| GET | `/api/v1/devices` | List devices currently pending provisioning |
| POST | `/api/v1/devices` | Register a new device (serial + optional metadata) |
| PUT | `/api/v1/devices/<serial>` | Edit an existing allowlist entry (`mac`, `description`, `image`, `config_file`). `404 device_not_found` if the serial doesn't already exist — this route never creates one |
| DELETE | `/api/v1/devices/<serial>` | Remove a device from the allowlist. `409 active_session_exists` if a `ProvisioningSession` is still active — cancel it first (see below). Also clears that serial's `DeviceLogEntry` rows, regardless of age — see [database.md](database.md), "Log Retention & Data Minimisation" |
| GET | `/api/v1/devices/<serial>` | Get a pending device's status (not history — see `/api/v1/log`) |
| GET | `/api/v1/devices/sessions` | List all active provisioning sessions |
| GET | `/api/v1/devices/sessions/<serial>` | Get the active provisioning session for a device |
| DELETE | `/api/v1/devices/sessions/<serial>` | Cancel a stale `ProvisioningSession` — admin-initiated only, and only once it's been quiet for longer than `SESSION_STALE_AFTER_MINUTES` (default 60); `409 session_not_stale` otherwise, `404 session_not_found` if there's no active session for that serial. Writes a `ProvisioningLog` row (`event='provision_cancelled'`) before deleting the session; `DeviceLogEntry` rows are left in place, same as a failure — see [database.md](database.md), "Stale sessions" |
| GET | `/files/images` | List uploaded OS images (auth required) |
| POST | `/files/images` | Upload an OS image (auth required). Optional `sha256` form field — if given, must match the uploaded bytes or the upload is rejected (`422 hash_mismatch`); if omitted, the hash is computed and stored automatically |
| GET | `/files/images/<filename>` | Download an OS image — unauthenticated, served to devices during ZTP |
| PUT | `/files/images/<filename>` | Update the stored sha256 for an image (auth required) |
| DELETE | `/files/images/<filename>` | Delete an OS image (auth required) |
| GET | `/files/configs` | List uploaded config files (auth required) |
| POST | `/files/configs` | Upload a config file (auth required). Same optional `sha256` form field behavior as image upload |
| GET | `/files/configs/<filename>` | Download a config file — unauthenticated, served to devices during ZTP |
| PUT | `/files/configs/<filename>` | Update the stored sha256 for a config file (auth required) |
| DELETE | `/files/configs/<filename>` | Delete a config file (auth required) |
| PUT | `/api/v1/provision-complete` | Device reports provisioning outcome; deletes the `ProvisioningSession` row, writes a `ProvisioningLog` row. On a successful outcome (`event=provision_complete`), also immediately deletes that serial's `DeviceLogEntry` rows, ahead of the normal `log_retention_days` purge — see [database.md](database.md), "Log Retention & Data Minimisation". The `devices` allowlist row is untouched. POST is also accepted for testing/debugging. |
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
| GET | `/api/v1/device-logs` | List raw device log entries (auth required); optional `serial` query filter. Optional `after_id` returns only rows with `id` greater than the given value, newest first, same as without it — for polling clients that already hold everything up to that id and want just what's new, instead of re-fetching the whole (potentially long) list every poll |
| POST | `/api/v1/device-logs` | Append a raw device log entry. Two body shapes: `{serial, message}` (the ZTP script's `log_to_server()`, `source='script'`) or `{ip, message}` (rsyslog's `omhttp` action, `source='syslog'`, IP best-effort correlated to a `ProvisioningSession` via `find_active_session_by_ip`). Either shape may also include an optional `state`: if given, it must be one of `PROVISIONING_STATES` (`422 invalid_state` otherwise) and is applied directly to the correlated session, skipping pattern matching entirely; without it, `message` is run through `drawbridge/device_events.py`'s `detect_state()` instead — see [logging.md](logging.md) |
| GET | `/api/v1/kea-logs` | List Kea's own DHCP server log entries (auth required). Optional `after_id`, same polling semantics as `GET /device-logs`. No `serial` filter — these lines carry no device correlation, unlike device logs — see [logging.md](logging.md) |
| POST | `/api/v1/kea-logs` | Append a Kea log entry, `{message}`. Called by `drawbridge-rsyslog`'s second `omhttp` action, fed by a host-side rsyslog rule (`kea/rsyslog-kea-forward.conf`) forwarding Kea's local syslog output — open route, same posture as `POST /device-logs` |

Every `/api/devices`, `/api/log`, `/api/users`, `/api/settings/*`, and
`GET /files/*` list / `POST /files/*` upload / `PUT /files/*` hash-edit /
`DELETE /files/*` delete route requires an authenticated session — see
[authentication.md](authentication.md).
`GET /files/<type>/<filename>` download endpoints are unauthenticated so that
IOS XE devices can fetch images and configs during ZTP without
credentials; access is restricted at the network level (provisioning VLAN).
`/api/provision-request`, `/api/provision-complete`, `POST
/api/device-logs`, and `POST /api/kea-logs` are all open routes called by
devices or rsyslog on their behalf (never operators) — gated by the
serial/IP lookup itself (and, at the perimeter, network isolation), not by
caller identity. `POST /api/kea-logs` specifically has no lookup to gate
on at all (no serial/IP in a Kea log line), but is only ever reachable from
`drawbridge-rsyslog` inside the pod's own loopback network namespace, not
from the provisioning VLAN. There is no secret an anonymous, unregistered
device could hold, so no auth decorator applies to any of these routes; see
[decisions.md](decisions.md) for the reasoning and the explicitly-accepted
serial-enumeration non-goal. `GET /api/device-logs` and `GET /api/kea-logs`
are the exceptions in this group and do require an authenticated session,
same as the other GET/list routes above.

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
  file to fetch); a `ProvisioningSession` row is created
- `404 device_not_found` → unknown serial; script exits, does not proceed
- `422 missing_parameter` → `serial` missing from the query string
