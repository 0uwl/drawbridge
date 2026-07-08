# Decisions and Constraints

- **No sZTP.** Cisco's RFC 8572 implementation requires MASA-issued Ownership
  Vouchers per device — too operationally complex for an internal provisioning
  VLAN. The threat model here (physically isolated VLAN, internal deployment)
  does not justify it.

- **Fail closed everywhere.** If Drawbridge is unreachable when the ZTP
  script phones home, the script gets no response and exits without
  provisioning — the device is left with its generic DHCP lease and
  nothing else. Devices wait and retry — they are not provisioned with
  unvalidated config.

- **Serial number over MAC.** The device's own serial (read via `show
  version`) is the canonical allowlist identifier. MAC is logged for audit
  but not used for lookup.

- **SQLAlchemy ORM.** `Device`, `ProvisioningLog`, `Setting`, and `User` are
  SQLAlchemy models (`drawbridge/models.py`) rather than plain SQL. Sessions
  are request-scoped and short-lived — opened on first use within a request
  and closed at teardown, not held open across a request's other work. See
  [database.md](database.md).

- **Drawbridge is not an inventory system — serials/MACs don't persist
  indefinitely.** The `Device` allowlist row persists until an operator
  explicitly `DELETE`s it (that call itself refuses while a
  `ProvisioningSession` is still active for that serial — this is what
  actually prevents concurrent/duplicate provisioning). What's transient is
  the `ProvisioningSession`, deleted the moment `/api/provision-complete`
  fires; a `ProvisioningLog` row (timestamp, image, config file) is written
  in its place, itself subject to a retention window. This is a deliberate
  scope boundary, not an oversight — asset/inventory tracking is a
  different problem with different data-retention requirements, and
  bolting it on here would push Drawbridge toward holding data it has no
  operational need for.

- **Provisioning gate moved from DHCP-level (Kea hook) to script-level.** A
  native `leases4_committed` Kea hook was built and found broken in a
  security-relevant way — `ParkingLotHandle::unpark()` doesn't honor a DROP
  decision, only `drop()` does (see [kea-hook-findings.md](kea-hook-findings.md))
  — and it dragged in a hard PostgreSQL requirement for Kea's own
  hosts-database, since `host_cmds`' `reservation-add`/`reservation-del`
  have no SQLite/memfile equivalent. Given the threat model (a physically
  isolated provisioning VLAN, not internet-facing), a real attacker on that
  VLAN doesn't need Kea's cooperation anyway — it can self-assign an
  address and hit Drawbridge's HTTP endpoints directly — so withholding the
  DHCP *lease* itself buys little real protection. The gate now lives in
  the ZTP script: an unregistered device still gets a normal lease and the
  generic script, but the script phones home with its own serial (from
  `show version`, never a DHCP option) before doing anything real, and
  Drawbridge approves/denies there. Simpler, no native hook, no Postgres
  requirement for Kea, same practical protection for this threat model.
  **Accepted non-goal:** `/api/provision-request` is an open route (no auth
  decorator — there's no secret an anonymous device could hold), so an
  attacker on the VLAN could enumerate serials via its 404-vs-200 response
  to fingerprint which devices are registered. Not defended against for
  alpha — a 404 leaks no config/image/script content, and this attacker
  already doesn't need Drawbridge's cooperation for anything more
  damaging. Revisit if the threat model ever includes a less-trusted VLAN.

- **Log retention is an admin-configurable DB setting, default 30 days,
  purged lazily.** `Setting(key='log_retention_days')` is changeable at
  runtime via `/api/settings/log-retention` rather than requiring a
  redeploy; an admin can explicitly opt into `indefinite` retention. Purging
  happens inline on the next `ProvisioningLog` insert rather than via a
  separate scheduled job — no new background process, consistent with "no
  external message queue" below, at the cost of expired rows lingering
  briefly on idle deployments.

- **Pluggable SQLite/PostgreSQL backend; SQLite forced to a single
  Gunicorn worker rather than migrated wholesale to Postgres.** A real,
  reproducing multi-worker race on SQLite's first-run bootstrap was found
  under Gunicorn's post-fork worker model (every worker independently
  racing `CREATE TABLE`/seed rows/admin bootstrap against a fresh
  `DATABASE_PATH` — see [kea-hook-findings.md](kea-hook-findings.md) #5).
  Since Drawbridge's own traffic was never throughput-bound (a handful of
  devices, a few admin users), removing the extra worker processes fixes
  the root cause more simply than adding a database server: `DATABASE_PATH`
  pointing at a bare filesystem path (SQLite, the default) forces
  `workers = 1` in `gunicorn.conf.py`, which is what makes the
  `fcntl.flock`-based bootstrap lock in `drawbridge/db.py` unconditionally
  sufficient. Setting `DATABASE_PATH` to a `postgresql+psycopg://...` URL
  instead allows multiple workers (`WORKERS` env var), using the same
  bootstrap-lock structure with a `pg_advisory_lock` in place of the file
  lock. See [database.md](database.md) ("Concurrency under multiple
  Gunicorn workers").

- **Flask-Login over Flask-Security-Too.** Flask-Login only tracks the
  current session (`current_user`, `login_user()`, `@login_required`) and
  has no opinion on how a user is authenticated. That seam is deliberate:
  local password login and the planned SAML SP integration both just call
  `login_user()` after validating credentials differently. A
  batteries-included extension would assume password auth as the primary
  flow and fight the SAML addition later.

- **`User` schema is SAML-ready ahead of SAML being built.** `auth_source`,
  `saml_issuer`, and `saml_subject` are on the `users` table now, and
  `password_hash` is nullable, so local and SAML accounts coexist
  indefinitely without a future migration. The actual SAML SP (`python3-saml`,
  new routes in `drawbridge/api/auth.py`) is not implemented yet. See
  [authentication.md](authentication.md).

- **No external message queue.** The ZTP script's phone-home call is a
  plain synchronous HTTP request/response — there's no async coordination
  problem to solve, and a message queue would add complexity with no
  benefit at this scale.

- **Rootless Podman for the Drawbridge container.** Kea cannot run rootless
  (requires raw socket for DHCP broadcast) so it runs as a native systemd
  service. The Drawbridge container has no such constraint and runs rootless
  under the `drawbridge` user with lingering enabled.

- **Kea Control Agent on 127.0.0.1:8081.** Default Kea port is 8080, which
  conflicts with Drawbridge. Control Agent is bound to loopback only.

- **Facts-first provisioning is deferred, not rejected.** An idea was raised
  for a two-phase flow: a small facts-collector script fetched first, which
  reports device facts (e.g. version/platform) to Drawbridge and gets back
  the appropriate provisioning script to hand off to, rather than one static
  script for all devices. This is a reasonable pattern for heterogeneous
  fleets, but it's real Day-0 provisioning logic — the same category the
  alpha `ztp-base.py` stub deliberately excludes (see alpha.md step 5) — and
  it requires schema/API additions alpha doesn't have: a version/platform
  field on `Device`, and a new endpoint (or facts parameter) for the
  fetch-then-select round trip, plus a second HTTPS/cert-validation hop.
  Scope this once real per-device provisioning logic is built and tested
  against hardware, not before.

- **C9200CX network stack isolation — `/api/provision-complete` accepts PUT.**
  Python scripts running on the C9200CX are entirely isolated from the device's
  own network stack; direct socket calls from the ZTP script fail. The
  workaround is to write the JSON payload to the device filesystem and issue
  `copy flash:status.json http://<drawbridge>/api/provision-complete` from IOS
  XE CLI (via `cli.execute()` in the script). IOS XE's `copy` command issues a
  PUT request, so the endpoint accepts PUT as its primary method. POST is also
  accepted for development and testing. The `Content-Type` header is not
  guaranteed to be set by `copy`, so the endpoint parses the body regardless of
  content type (`force=True`). This constraint applies to all network I/O in the
  ZTP script on C9200CX — image and config downloads must similarly be triggered
  via `cli.execute("copy http://... flash:")` rather than Python's `urllib` or
  `requests`.
