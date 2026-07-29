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

- **DHCP client classification by vendor (Option 60) is for per-vendor
  options, not access control.** Cisco IOS-XE and Juniper Junos ZTP boot
  differently — different DHCP options pointing at different boot
  mechanisms — so `kea/kea-dhcp4.conf` uses Kea's native client
  classification to give each vendor its own `option-data` rather than one
  global block that can't serve both. Only `cisco-devices` has real options
  for alpha (`scripts/ztp_script.py`); `juniper-devices` is admitted to the
  pool already so Junos ZTP support is additive later, but isn't built —
  out of scope for alpha. This is unrelated to the actual security gate
  (the script's phone-home call): Option 60 is client-supplied and
  trivially spoofable, so pool admission by vendor class is a DHCP-options
  routing decision, not an allowlist. See [kea.md](kea.md).

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
  alpha — a 404 leaks no config/image content, and this attacker
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

- **Facts-first provisioning: self-reporting only, no script hand-off.**
  An earlier version of this idea proposed a full two-phase flow — a
  facts-collector script fetched first, reports device facts, and gets back
  a *different* script to hand off to for heterogeneous fleets. That's real
  Day-0 provisioning logic, the same category the alpha `ztp_script.py` stub
  deliberately excludes (see alpha.md step 5), and per-device script
  selection was itself explicitly removed, not deferred (see below) — so
  the hand-off half stays out of scope. What is built: after
  `/provision-request` approves a session, the script self-reports its own
  `model`/`version` via `PUT /provision-request/facts` (see
  [api.md](api.md)), recorded onto that `ProvisioningSession` row (see
  [database.md](database.md)) purely for operator visibility (Active
  Sessions UI) — nothing server-side branches on it yet. Deliberately kept
  on `ProvisioningSession`, not `Device`: transient like the rest of that
  row, gone the moment the session is, consistent with "Drawbridge is not
  an inventory system" above. No second HTTPS/cert-validation hop either —
  it reuses the same session-pinned transport (`_put_json`) `report_status`/
  `log_to_server` already use.

- **Per-device script selection removed (v0.3.1), not deferred like
  facts-first above — it never actually drove anything.** `Device.script`,
  a `default_script` setting, and a full `/files/scripts/*` upload API let
  an operator pick a different ZTP script per device, but Kea's Option 67
  `boot-file-name` has always hardcoded the delivered filename to
  `ztp_script.py`, and the script itself never read a "next script" field
  back from `/api/provision-request` to chain-fetch anything else. No
  matter what an admin uploaded under a different name, devices only ever
  fetched whatever was literally named `ztp_script.py` — the whole
  selection layer was dead weight, not a working feature being cut for
  scope. Removed outright rather than wired up, since the same
  "facts-first" reasoning above still applies to *real* per-device script
  selection: it's Day-0 provisioning logic that needs hardware-tested
  design, not something to bolt onto an unused upload API. `ztp_script.py`
  is now served via `drawbridge-bootstrap`'s bind mount instead (see
  "HTTPS cert trust on C9200CX" below) — one script, no DB-backed
  selection at all, but still editable in place per deployment (it has
  deployment-specific constants — `DRAWBRIDGE_HOST`, `DRAWBRIDGE_CA_CERT_PEM`
  — an operator must set), so this isn't a return to a build-time-only
  artifact the way images/configs were considered and rejected for.

- **C9200CX network stack isolation — `/api/provision-complete` accepts PUT.**
  Python scripts running on the C9200CX are entirely isolated from the device's
  own network stack; direct socket calls from the ZTP script fail. The
  workaround is to write the JSON payload to the device filesystem and issue
  `copy flash:status.json https://<drawbridge>/api/provision-complete` from IOS
  XE CLI (via `cli.execute()` in the script). IOS XE's `copy` command issues a
  PUT request, so the endpoint accepts PUT as its primary method. POST is also
  accepted for development and testing. The `Content-Type` header is not
  guaranteed to be set by `copy`, so the endpoint parses the body regardless of
  content type (`force=True`). This constraint applies to all network I/O in the
  ZTP script on C9200CX — image and config downloads must similarly be triggered
  via `cli.execute("copy https://... flash:")` rather than Python's `urllib` or
  `requests`.

  **Addendum — HTTPS cert trust on C9200CX (beta section 3).** IOS XE's
  HTTPS client does not silently accept an unverifiable certificate: without
  a pre-authenticated trustpoint, `copy https://...` either blocks on an
  interactive accept/reject prompt (nothing to answer it — Guestshell's
  `cli.execute()` isn't a TTY) or fails outright. `scripts/ztp_script.py`
  therefore imports `DRAWBRIDGE_CA_CERT_PEM` into a trustpoint via
  `crypto pki authenticate` (`_ensure_c9200cx_trustpoint()`) before its first
  `copy https://` call. A self-signed cert is a valid trust anchor on its
  own for this purpose — no real CA hierarchy is required, `crypto pki
  authenticate` just needs to be pointed at whatever cert Drawbridge is
  actually serving. **Resolved (v0.3.1) for the one fetch that can't use a
  trustpoint at all:** the very first fetch — the ZTP script itself, via DHCP
  Option 67 `boot-file-name` — happens before any script code runs, so
  there's no opportunity to import a trustpoint ahead of it, and Cisco's
  Classic ZTP documentation describes this fetch with no
  certificate-validation step at all (unlike "Secure ZTP," a different,
  SUDI-based Cisco mechanism this project deliberately doesn't use — see "No
  sZTP" above). Rather than treat that as an open risk to work around later,
  v0.3.1 removes the cert-trust question from this fetch entirely: Option 67
  now points at a separate, deliberately plain-HTTP `drawbridge-bootstrap`
  container (`Containerfile.bootstrap`, port 8090) instead of Drawbridge's
  own HTTPS listener, structurally matching what Cisco's own docs already
  assume for this step. Everything the script does *after* that first fetch
  — phone-home, completion callback, image/config download — is unaffected
  and still goes through the trustpoint-based validation path described
  above. **Still unconfirmed without lab hardware:** whether the boot agent's
  plain-HTTP fetch of `boot-file-name` itself behaves as assumed — same
  posture as other C9200CX-dependent behavior in this file: documented as
  manually verified, not something this diff can close out.

- **`files.py`'s file-serving routes use `<path:filename>`, not
  `<string:filename>`.** `<string:...>` excludes `/` from what it matches, so
  a traversal-looking request like `/files/images/../../main.py` (containing
  literal `/`) doesn't match `/files/images/<filename>` at all — Werkzeug's
  router falls through to `main.py`'s SPA catch-all (`/<path:path>`) instead,
  which never 404s by design (it serves the exact static asset or falls back
  to `index.html`), silently returning `200` with the SPA shell rather than
  the file-serving blueprint's own `404 file_not_found`. This only becomes
  visible once `drawbridge/static/` actually exists (i.e. the frontend has
  been built — the normal state in production and from step 8 onward), which
  is why it wasn't caught earlier. `<path:filename>` fixes it correctly:
  the request now reaches this blueprint's own `get_file()` DB lookup, which
  always misses for a traversal attempt (stored filenames are sanitized via
  `secure_filename()` at upload time and can never contain `/` or `..`), so
  it 404s cleanly. `send_from_directory`'s own `safe_join` is a second,
  independent layer even if that lookup somehow passed. Don't revert this to
  `<string:filename>` for a "no slashes in filenames" cleanliness reason —
  it reopens exactly this gap.

- **Bootstrap admin password sources aren't treated equally for forced
  reset.** Three ways to seed the bootstrap admin's initial password now
  exist (see [authentication.md](authentication.md)): a systemd credential,
  the `ADMIN_PASSWORD` env var, or the default random-generated password.
  Only the systemd credential skips `User.must_reset_password`. The
  dividing line isn't "who chose the password" (an operator vs. the app
  itself) — it's whether the plaintext ends up somewhere durable and
  outside the app's control. A systemd credential is exposed to the
  process only via a private, per-invocation `$CREDENTIALS_DIRECTORY` and
  never appears in `podman inspect`/`systemctl show`/`/proc/*/environ`, so
  there's nothing gained by forcing a reset. `ADMIN_PASSWORD` has to sit in
  plaintext somewhere durable (a Quadlet unit's `Environment=` line, a
  `.env` file) for the container to read it on every restart. The
  random-generated default was originally exempted on the theory that
  printing it once and never persisting it *within the app* was enough —
  but that conflated app-level persistence with infrastructure-level
  persistence: container stdout routinely ends up retained indefinitely in
  journald or shipped to log-aggregation systems with broader read access
  than a single config file, so a high-entropy password printed to stdout
  is exposed the same way an env var is, just through a different channel.
  Both now force a reset. Forced reset uses a dedicated
  `POST /api/auth/reset-password` route rather than the existing
  session-based `/change-password`, since no session exists yet at that
  point — `login()` validates credentials but deliberately withholds a
  session while the flag is set.

- **`install.sh`'s curl-pipe fetch defaults to `main`, not a release tag —
  revisit once real releases start.** When `install.sh` is run via
  `curl | bash` there's no sibling `kea/` directory to read from, so it
  fetches `kea/kea-dhcp4.conf`/`kea/kea-ctrl-agent.conf` and the Quadlet
  unit files from `raw.githubusercontent.com` at `$DRAWBRIDGE_REF` (env
  var, default `main`) via `RAW_BASE`. The ref has to be named twice to
  actually change it — once in the curl URL fetching `install.sh` itself,
  once in `DRAWBRIDGE_REF` so `install.sh`'s own fetches match — a piped
  script can't introspect the URL it was downloaded from, so there's no way
  to specify it only once (see `README.md`'s "Installation" section for
  both forms). A CI check (`.github/workflows/ci.yml`, "Verify README.md /
  install.sh default ref match") fails the build if `README.md`'s default
  one-liner and `install.sh`'s default `DRAWBRIDGE_REF` disagree — but that
  only catches the two copies disagreeing with *each other*, not the
  underlying problem with the *default* itself: `main` is still a moving
  target, so
  the plain one-liner silently serves whatever lands on that branch on the
  day it's run, not a fixed point in time. There's no release process yet
  (see [alpha.md](../alpha.md)), so this is accepted for now. Once real
  releases start post-alpha, default `DRAWBRIDGE_REF` to a resolved release
  tag instead of `main` — e.g. via the GitHub Releases API — so the plain
  one-liner installs a fixed, reproducible version rather than
  tip-of-branch.

- **`DRAWBRIDGE_PORT` controls where the app is *reached*, but several other
  hardcoded port numbers aren't wired to it (the "hardcoded 8080s").**
  `dev.sh`'s `flask run --port` and `frontend/vite.config.js`'s dev-proxy
  target read this env var (default `8080`). Outside local dev, though, it
  only actually controls Gunicorn's own bind when `TLS_DISABLED` is set
  (`drawbridge/gunicorn.conf.py`) — in normal operation (v0.3.2,
  `drawbridge-nginx` terminating TLS) Gunicorn always binds a fixed
  internal-only `127.0.0.1:8078`, and `DRAWBRIDGE_PORT` is just the value
  every one of the spots below has to be kept in sync with by hand if it
  ever changes: `container/nginx-drawbridge.conf` (hardcodes its own
  external `8080`, the plaintext-responder's internal port, and the
  `127.0.0.1:8078` it proxies to — no templating, same posture as
  `container/rsyslog-drawbridge.conf` below), `container/rsyslog-drawbridge.conf`
  (`serverport="8078"` on both its `omhttp` actions), `kea/kea-dhcp4.conf`'s
  Option 67 boot-file URL, and `scripts/ztp_script.py`'s own `DRAWBRIDGE_PORT`
  constant. The Kea config and rsyslog/nginx configs are static files (no
  templating layer — see [kea.md](kea.md)), and the ZTP script runs on the
  device itself (IOS XE Guestshell), an entirely separate machine with no
  access to the server's environment — none of them can read the env var
  even in principle. Not worth solving with a templating system for values
  expected to change rarely, if ever, in a given deployment — but worth
  flagging clearly at each spot (and here) so none of them is mistaken for
  a single source of truth. Kea Control Agent's `127.0.0.1:8081` above is
  the same kind of fixed, internal-only constant, for the same reason.
