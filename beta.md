# Beta

Drawbridge Beta requires a fresh install, there is no in-place alpha→beta
upgrade. New columns (`User.claim_token`) and tables (`DeviceLogEntry`) rely
on `create_all()` alone

## 1. Migrate frontend from JavaScript to TypeScript 7 — ✅ Complete

**Status: implemented.** All of `frontend/src/` (utils, api clients, six
Pinia stores, router, main, all 8 `.vue` files) is TypeScript, with a new
shared `frontend/src/types.ts`, `frontend/tsconfig.json`, and a `frontend`
type-check job in `.github/workflows/ci.yml` gating `publish`. One deviation
from the plan below, found during implementation: **TypeScript 7 itself
isn't actually used** — see the clarification in the next paragraph.

`frontend/src/` was 100% plain JavaScript before this — no `tsconfig.json`, no type
checking at all. Several beta items below touch shared state shape across
store/component boundaries (the claim-token field threading through
`stores/auth.js`, new `stores/deviceLogs.js` mirroring `stores/log.js`,
confirm-password fields across three forms) — exactly where a renamed or
missing field breaks silently at runtime instead of at build time. TypeScript
7 (the Go-ported compiler, shipped stably as `typescript@7`) is a drop-in
replacement for `tsc`'s CLI/language service in general, but **not yet for
this stack specifically**: `typescript@7`'s package dropped the classic
`lib/tsc`-style compiler-API surface that `vue-tsc` (the only real option for
type-checking `.vue` SFCs) still requires — confirmed by installing both and
watching `vue-tsc --noEmit` fail with `ERR_PACKAGE_PATH_NOT_EXPORTED`, not a
guess. Pin `typescript` to `^6.0.3` (last version with the classic package
layout) instead, and revisit once `vue-tsc`/`@vue/language-tools` ship
support for TS7's new API surface — tracked as a follow-up, not blocking
this migration.

**Decision: incremental in-place migration, not a rewrite.** `allowJs: true`
keeps the app building at every commit — no separate "TS branch" to land in
one PR.

**Implementation plan:**

- Add `typescript` (pinned `^6.0.3` — not TS7, see the clarification above)
  and `vue-tsc` to `frontend/package.json` devDependencies. Vite's transform (esbuild/rolldown)
  strips types but never checks them, so `vue-tsc --noEmit` is the actual
  type-check step, and it needs to run somewhere real, not just trusted to
  editor tooling.
- New `frontend/tsconfig.json`: `strict: true`, `allowJs: true` (drops once
  the last `.js` file converts), `vueCompilerOptions` pointing `vue-tsc` at
  `.vue` files.
- Convert file-by-file, leaves of the dependency graph first: `utils/format.js`,
  `utils/fileTypes.js` → `api/client.js`, `api/filesClient.js` →
  `stores/*.js` (six files) → `router/index.js`, `main.js` → each `.vue`
  file's `<script setup>` block gets `lang="ts"` last (8 files, depend on
  everything above already being typed).
- Land this **before** the frontend-touching beta items below (claim-token
  field, confirm-password fields, `stores/deviceLogs.js`), not after — those
  are new code in the same files this migration renames; going first means
  they're written as `.ts`/`lang="ts"` directly instead of being migrated
  twice.
- CI: add a frontend job to `.github/workflows/ci.yml` (there isn't one
  today — `test` is Python-only) running `npm ci && npm run type-check`, a
  new `package.json` script wrapping `vue-tsc --noEmit`.
- Docs: `docs/frontend.md` — drop any "plain JS" wording, note the `.ts`
  convention for new files.

No ESLint/Prettier added alongside this — not asked for, existing repo has
neither today, out of scope here. `# ponytail: allowJs stays until the last
.js file converts, flip to false at that point`.

## 2. Password & Session Security Improvements — mostly ✅ complete, 2.1 deferred

**Status: 2.2–2.9 implemented.** Two deviations found during implementation,
both against the plan below:

- **2.1 (cookie flags) is deferred, not implemented.** `SESSION_COOKIE_SECURE
  = True` makes browsers drop the session cookie over plain HTTP. Section 3
  (Drawbridge terminating its own TLS) hasn't landed yet — shipping 2.1 now
  would silently break every plain-HTTP deployment (login 200s, but the
  browser discards the cookie, so the next request 401s). Revisit once
  Section 3 ships.
- **2.5 (admin-triggered reset) reuses the claim-token flow (2.4), not
  `/auth/reset-password`.** As written below, `reset_password()` requires
  checking a `current_password` against `password_hash` — impossible once an
  admin nulls the hash (it would 400 unconditionally). Implemented instead:
  `POST /api/users/<id>/reset-password` nulls `password_hash` and issues a
  fresh `claim_token`; the account re-claims via the same token-gated `POST
  /auth/claim` path 2.4 already hardens. See
  [docs/authentication.md](docs/authentication.md) for the shipped behavior.

Alpha's password handling (see [docs/authentication.md](docs/authentication.md))
made several tradeoffs that were explicitly scoped to "internal,
network-isolated deployment, single admin bootstrapping accounts by hand."
Beta widens exposure (more operators, longer-lived deployments, possibly
less-trusted networks), so the tradeoffs below should be revisited. Nothing
here is a rewrite — each item is a small, targeted change against the
existing Werkzeug/Flask-Login setup.

### 2.1. Set cookie security flags explicitly

`drawbridge/main.py` never sets `SESSION_COOKIE_SECURE` or
`SESSION_COOKIE_SAMESITE` — the app relies entirely on Flask's defaults
(`SECURE=False`) plus an unstated assumption that a reverse proxy terminates
TLS in front of it. Nothing in [docs/deployment.md](docs/deployment.md)
actually requires that proxy to exist. Set both explicitly once beta has a
real deployment target:

```python
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
```

Set both **unconditionally** whenever `not app.testing` — no new gating env
var needed. The HTTPS-transport section below makes Drawbridge terminate its
own TLS by default (self-signed cert, always on), so there's no longer an
"unencrypted Drawbridge" deployment mode to gate against. Without this, the
session cookie established right after a password check can be read off an
unencrypted connection.

### 2.2. Rate-limit `/auth/login`, `/auth/claim`, `/auth/reset-password`

None of the three password-entry routes in `drawbridge/api/auth.py` have any
throttling. `/auth/login` is brute-forceable at whatever rate the network
allows, and `/auth/claim`'s accepted "first claim wins" race (see
authentication.md: "accepted as a known tradeoff... Revisit if Drawbridge's
threat model changes") gets meaningfully worse without a rate limit — an
attacker only needs to win one race, and can attempt it as fast as the
server accepts requests. Add `Flask-Limiter` (not yet a dependency) on these
three routes; a per-IP limit is enough for alpha's "no self-service signup"
model.

**Implementation plan:** `Limiter(key_func=get_remote_address, storage_uri='memory://')`,
`limiter.init_app(app)` in `main.py`, `@limiter.limit(...)` on `login`,
`claim`, `reset_password`. In-memory storage is correctly scoped for the
SQLite/single-worker deployment ([docs/database.md](docs/database.md)); under
Postgres/multi-worker it's per-worker, not global, so a determined attacker
gets `WORKERS`× the nominal limit. Accepted for now rather than adding Redis
speculatively — `# ponytail: per-worker limiter, shared storage if
multi-worker rate limiting matters`.

### 2.3. Enforce a minimum password length

`claim()`, `change_password()`, and `reset_password()` in `auth.py` accept
any non-empty string as `new_password` — a one-character password is valid
today. Add a length check (e.g. 8+ chars) alongside the existing
`if not username or not password` guards. This is the cheapest possible
improvement here and there's no reason to defer it further than alpha.

### 2.4. Replace the claim race with an admin-issued token

Per authentication.md, `/auth/claim` is deliberately passwordless-until-claimed:
whoever `POST`s a known username first sets its password. Alpha accepted
the resulting account-takeover race because only admins create accounts on
an isolated network. That assumption is exactly what beta is expected to
relax. Replace it with a one-time claim token generated at account creation
(`POST /api/users`) and required by `/auth/claim`, so claiming needs
something out-of-band (the token, shared by the admin) rather than just
guessing/racing on the username.

**Implementation plan:** new `User.claim_token` column (nullable), generated
via `secrets.token_urlsafe(32)` in `queries.create_user()`. Returned once in
`POST /api/users`'s response payload only — never included in `_user_dict`
(`settings.py`), so it's never re-shown after creation. `claim()` requires
`token` in the request body, checks it against `user.claim_token`, nulls it
on success (single-use). Frontend: add a token input field to the claim
form and thread it through `stores/auth.js`'s `claim()` action.

### 2.5. Admin-triggered password reset, not delete-and-recreate

Today, per authentication.md, "an admin who needs to let a user re-claim
their account deletes and recreates it" — there's no reset path once an
account is claimed. That's a usability gap that pushes toward a security
anti-pattern: admins reaching for delete+recreate under time pressure is
more error-prone than a dedicated reset. Add an admin-only
`POST /api/users/<id>/reset-password` that nulls `password_hash` and sets
`must_reset_password`, reusing the existing reset-password flow in
`auth.py` rather than inventing a new one.

### 2.6. Log failed-auth events without the credentials

Every failure path in `auth.py` (`login`, `reset_password`, `claim`,
`change_password`) calls `error_response(..., silent=True)`, which skips
`logger.error()` entirely (see `drawbridge/utils.py:17-20`). That's correct
for not logging passwords, but it also means there's currently no signal
anywhere to notice repeated failed logins against an account. Add a single
non-silent log line on failure (username + event type only, never the
password) so brute-force/claim-race attempts are at least visible in logs
before rate limiting (#2.2) catches them.

### 2.7. Pin the password hashing method explicitly

`generate_password_hash()` calls in `auth.py`/`db.py` don't pass a `method`
argument, so hash strength for *newly created* hashes depends on whatever
Werkzeug's default happens to be on the installed version. Pin it
explicitly (`generate_password_hash(password, method='scrypt')`) so a future
Werkzeug upgrade can't silently change the hashing strength for new
passwords out from under a pinned `requirements.txt` version. `check_password_hash`
reads the method from the stored hash string, so this doesn't require a
migration of existing hashes.

### 2.8. Check `login_user()`'s return value (found during beta planning, not originally scoped)

`User.is_active` is a real mapped column (default `True`) and correctly
shadows Flask-Login's `UserMixin.is_active` (verified directly — it's in
`User.__dict__`, ahead of `UserMixin` in the MRO), so `login_user()` already
refuses to log in a deactivated account internally. The actual bug:
`login()` (`auth.py`, `login_user(user)` call) and `reset_password()` (same
call) never check that call's return value. If `is_active` were ever set
`False`, `login_user()` returns `False` (no session established) but the
route still falls through to `success_response('Logged in', ...)` — a lying
200. Fix: check the return, return `error_response('Account is deactivated',
'account_inactive', code=403, silent=True)` on `False`. Nothing today
actually sets `is_active=False` (no admin-facing deactivate control exists,
and building one isn't in scope here) — this just stops the column from
lying if something sets it in the future.

### 2.9. Add password-confirm fields to claim/reset/change-password forms

None of the three password-entry forms in `Login.vue`/wherever change-password
lives have a "confirm password" field today — only the store/backend
validation exists. Add the field client-side (simple equality check before
submit) alongside the new minimum-length enforcement (#3) and the claim-token
field (#4).


## 3. HTTPS transport between Drawbridge server and ZTP client

A core design pillar of Drawbridge is to make Classic ZTP more secure. One of these security improvements were to securely transport data between the ZTP server and the ZTP client, such as the image and the configuration. This was left out of the Alpha release but should be mandatory for the Beta so that Drawbridge can live up to its goal.

**Implementation plan:**

- **TLS termination — Drawbridge terminates its own TLS**, serving both
  device-facing (ZTP phone-home, file downloads) and GUI traffic through the
  same listener. New `drawbridge/tls.py`: `ensure_cert(cert_path, key_path)`,
  generating a self-signed cert on first run if the mounted paths don't
  exist, reusing `db.py`'s existing `fcntl.flock`-based first-run-bootstrap
  pattern rather than inventing new machinery. `cryptography` added to
  `requirements.txt` (no stdlib cert-generation option — the one justified
  new dependency here). `drawbridge/gunicorn.conf.py` calls `ensure_cert(...)`
  at **module level** (must run pre-fork, before the master binds the
  listening socket) and sets `certfile`/`keyfile` from new `TLS_CERT_PATH`/
  `TLS_KEY_PATH` env vars (default under `/app/data`, alongside
  `DATABASE_PATH`'s existing convention).
- **Optional GUI-facing reverse proxy**: an operator who wants a "real"
  ACME-issued cert for browser convenience may put their own reverse proxy in
  front of the GUI path only, re-terminating/re-encrypting to Drawbridge's
  own TLS listener. ZTP devices always talk directly to Drawbridge's own
  listener (self-signed or org-mounted cert) — never through that optional
  proxy, since Option 67's boot-file URL points at Drawbridge directly and
  isolated provisioning VLANs generally can't complete ACME challenges anyway.
- **Kea config**: `kea/kea-dhcp4.conf`'s `cisco-devices` Option 67
  `boot-file-name` moves from `http://` to `https://`.
- **`scripts/ztp-base.py`**: both request URLs move to `https://`. Add a
  `get_platform()` helper parsing the platform/PID field out of the same
  `show version` output already parsed for the serial (parse, don't
  probe-and-fall-back — a genuine network failure and "no network stack at
  all" look identical to a try/except probe). Replace today's binary
  `import cli` branch with a three-way dispatch:
  1. No `cli` → local-testing `urllib.request` fallback (unchanged mechanism).
  2. `cli` present **and platform is C9200CX** → `cli.execute('copy
     https://... flash:')` — this model's Guestshell has no usable network
     stack of its own (see [docs/decisions.md](docs/decisions.md), "C9200CX
     network stack isolation" — already correctly scoped to this specific
     model, not real hardware generally), so cert trust here is IOS-XE's own
     trust store; Drawbridge/Python cannot verify or override it.
  3. `cli` present **and any other real platform** → direct
     `urllib.request` + `ssl.create_default_context(cafile=<mounted CA cert
     path>)` — Guestshell has its own network stack on these models, so the
     script itself performs real, Python-controlled certificate verification
     against a mounted/bundled CA cert (new hand-maintained constant,
     mirroring `DRAWBRIDGE_HOST`'s existing "update by hand" note).
- Payload hash verification (see the section below) applies uniformly across
  all three branches, after the bytes land locally, regardless of which path
  fetched them.
- Docs: `deployment.md` (new `TLS_CERT_PATH`/`TLS_KEY_PATH` env vars, the
  self-signed default, the optional reverse-proxy pattern),
  `security-faq.md` ("Is my session cookie safe in transit?" — rewrite, no
  longer "depends"), `decisions.md` (short addendum to the C9200CX bullet
  covering the new platform branch), `architecture.md` (DHCP flow steps 3/7
  go from aspirational to actual).
- Tests: new `tests/test_tls.py` (`ensure_cert()` creates both files on an
  empty dir; second call is a no-op; files parse as valid PEM). Extend
  `tests/test_ztp_base.py` for the https:// URL, `get_platform()` parsing,
  and the three-way dispatch (mock `cli.execute` with stubbed platform
  strings for both C9200CX and another platform). Update
  `tests/test_kea_config.py`'s Option 67 assertion to `https://`.

## 4. SAML authentication

The beta should include SAML authentication to improve user handling and security. The groudwork has already been layed out for this in the Alpha.

**Implementation plan:** generic SP side only — no real IdP to wire against;
tests use `python3-saml`'s own fixtures/mock IdP. Add `python3-saml` to
`requirements.txt`; it needs OS-level build deps (`libxmlsec1-dev`,
`pkg-config`, `libxml2-dev`) in the `Containerfile`'s Python stage — a real
image-size/attack-surface increase, accepted since there's no lighter
alternative for SAML SP behavior. New `drawbridge/saml.py` with SP
settings/IdP metadata mounted the same way `/app/scripts` is mounted today.
Routes in `drawbridge/api/auth.py` per the plan already written in
[docs/authentication.md](docs/authentication.md): `GET /saml/metadata`,
`GET /saml/login`, `POST /saml/acs` (upserts `User` keyed on
`saml_issuer`+`saml_subject`, calls `login_user()`). New
`queries.get_or_create_saml_user()`. Docs: `authentication.md` moves this
section from "Planned" to implemented; `deployment.md` gets the new config
mount.

## 5. ZTP client logging — ✅ Complete

The Drawbridge server should be able to collect logs from ZTP devices and display them in the GUI. The ZTP script itself should log to the server and also configure the device to send its syslog messages to the server. The logs should be viewable in a separate view where it can be filtered by device or by clicking on the active session row to get that device's log flow. We should device if the log collection should be an optional sidecar rsyslog container or to have rsyslog installed and running inside the Drawbridge container, using a manager like s6-supervise.

**Status: implemented**, matching the plan below with two additions found
during implementation, neither a deviation from the decision itself:

- **Port 514 is privileged; the container stays non-root.** The original
  plan said "expose port 514 (UDP+TCP)" without addressing that the image
  runs as `USER drawbridge` (UID 1000) throughout — binding 514 directly
  would need root or `CAP_NET_BIND_SERVICE`. rsyslog listens on
  unprivileged `:10514` inside the container instead; Quadlet's
  `PublishPort=514:10514/udp`+`/tcp` does the remap at the network layer.
  No root init phase for s6-overlay, no new capability.
- **Syslog→serial correlation, needed for "filtered by device" to actually
  work for syslog rows.** The plan noted `serial` is nullable "since
  syslog may arrive before a serial is parseable" but didn't specify how
  it ever gets populated. `log_poller.py` matches each line's
  `%fromhost-ip%` against the active `ProvisioningSession.ip`
  (`queries.find_active_session_by_ip`) and stamps that session's serial
  when found — without this, syslog-sourced rows would never carry a
  serial.

The rsyslog output mechanism (file vs FIFO, left open in the plan below) is
a named pipe (`ompipe`) — see [docs/logging.md](docs/logging.md) for the
reasoning. Full design, API shape, and GUI wiring documented there;
`deployment.md` covers the port mapping and manual smoke test.

**Decision: in-container rsyslog via s6-overlay**, not a sidecar container —
one image to deploy/update, at the cost of adding a process supervisor to
the currently single-process `Containerfile` (today: bare
`CMD ["gunicorn", ...]`, no supervisor at all).

**Implementation plan, in dependency order:**

- **Container/supervisor plumbing (foundation, must land first):** add
  s6-overlay + rsyslog to `Containerfile`; convert to `ENTRYPOINT ["/init"]`
  with sibling s6 services for `gunicorn` and `rsyslog`; expose port 514
  (UDP+TCP). No unit test for the supervisor wiring itself (infra, not
  logic) — manual smoke test via `podman build`/`run`/`logger`, documented in
  `deployment.md`, same posture as the Quadlet validation below.
- **Server-side collection (greenfield — do not extend the existing
  provisioning-log stack):** the existing `Log.vue`/`stores/log.js`/
  `GET /api/log`/`ProvisioningLog` is a structured provisioning-*outcome*
  audit trail (event/image/config/ip/detail), unrelated to raw device
  syslog — it needs its own model, not a repurposing of that one. New
  `DeviceLogEntry` model (`serial` nullable — syslog may arrive before a
  serial is parseable, `source: 'script'|'syslog'`, `message`, `timestamp`),
  following `ProvisioningLog`'s shape/`as_dict()` convention.
  `queries.add_device_log_entry()`/`list_device_logs(session, serial=None)`.
  New `GET /api/v1/device-logs?serial=...` route. rsyslog writes to a local
  file/FIFO; a small poller service under s6 tails it and inserts via the
  existing `db.py` session machinery (simpler than getting rsyslog to speak
  SQLite directly, no new rsyslog output-module dependency). Frontend: new
  `stores/deviceLogs.js` (template: `stores/log.js`'s `loading`/`error`/data
  shape), new `views/DeviceLogs.vue`, router entry with
  `meta: { requiresAuth: true }`, click-through wired from `Sessions.vue`
  rows (the active-session join point, per this section's own wording above).
- **Device-side (depends on the previous two steps existing to test
  against):** `scripts/ztp-base.py` gains a `log_to_server()` helper, called
  at key points in `main()` (start, each fetch, hash-check result,
  completion), using the same three-way transport branch from the HTTPS
  transport section above. On real hardware, also configures the device's
  own syslog target to point at Drawbridge's exposed port. No realistic way
  to unit-test the actual on-device `logging host` config without hardware —
  documented as manually verified, same posture as other C9200CX-dependent
  behavior; `log_to_server()`'s call sequencing itself is testable by mocking
  the transport.
- Docs: new `docs/logging.md` (the topic is big enough not to bury in
  `architecture.md`), `deployment.md` (new port 514 mapping, new mount if the
  poller needs one, Quadlet snippet).

## 6. Payload integrity verification (image/config/script hash checks) — ✅ Complete

[docs/architecture.md](docs/architecture.md) already documents this as part
of the DHCP flow — step 3 ("Device fetches the ZTP script over HTTPS,
verifies server cert and payload hash") and step 7 ("real provisioning...
hash verification, config push — a later phase") — but `scripts/ztp-base.py`
is a stub for alpha and does neither. This is distinct from the HTTPS
transport item above: HTTPS protects data in transit, but a hash check
protects against a compromised/misconfigured server or a corrupted
download regardless of transport. Once beta implements HTTPS transport, add
hash verification of the fetched image/config/script against a
value Drawbridge serves alongside them, before the device acts on any of it.

**Implementation plan:** no new storage needed — `ZTPFile.sha256`
(`drawbridge/models.py`) is already a non-nullable column, already populated
at upload time (`files.py`'s `_handle_upload`), already in `as_dict()`. The
actual gap is exposing it to the device and having the script check it.
Bundle this with the pinning section below since both touch
`provision_request()`'s response shape: on success, extend the payload
beyond `device.as_dict()` with `image_sha256`/`config_sha256`/`script_sha256`,
looked up via a new small helper wrapping the existing `get_file()`. In
`scripts/ztp-base.py`, after any fetch (all three transport branches from the
HTTPS-transport section converge on "bytes now on local disk/flash"),
compute SHA-256 of the downloaded file and compare against these values
before acting on it; mismatch → fail closed (same posture as a 404 denial),
no completion report sent.

## 7. Pin device-facing requests to serial+MAC+IP, backed by Kea host reservations

Today, every device-facing route treats each request independently:
`create_provisioning_session()` ([drawbridge/queries.py:93-113](drawbridge/queries.py#L93))
is idempotent and silently overwrites `mac`/`ip` on every call to
`/api/provision-request`, `/api/provision-complete` never compares its
caller's IP against the session it's completing, and the file-serving `GET`
routes in `drawbridge/api/files.py` take no serial/session context at all —
anyone who knows a filename can fetch it without ever having called
`/provision-request`. Change this to a pin-on-first-use model:

- `provision-request` records (serial, mac, ip) once per session and
  rejects a mismatched mac/ip on subsequent calls for the same serial
  instead of overwriting them.
- `provision-complete` and the file-serving routes require an active
  `ProvisioningSession` for the target serial/file and reject requests
  whose IP doesn't match what's recorded on it.

**Correction found during implementation planning:** the file-serving check
above applies **only to `/files/images/<filename>` and
`/files/configs/<filename>`**, not `/files/scripts/<filename>`. The generic
ZTP script is fetched via DHCP Option 67 *before* any `ProvisioningSession`
exists — it's the same file for every device, fetched blind, with no serial
known yet. Requiring an active session there would break the boot bootstrap
itself. Scripts stay session-less on the serve path, consistent with them
carrying nothing confidential (already established above).

**Implementation plan:** `create_provisioning_session()`
(`drawbridge/queries.py:93-119`) changes from unconditional overwrite to
reject-on-mismatch. `provision_complete()` rejects if
`request.remote_addr != active.ip`. A new small shared helper (added to
`queries.py`, since both `leases.py` and `files.py` already import from
there) does the IP-match check, used by both blueprints instead of
duplicating it. Tests: rewrite `tests/test_lease_api.py`'s existing
idempotent-overwrite test to expect `409` on a mismatched repeat call, plus a
new test confirming a *matching* repeat call still succeeds (true
idempotency preserved on the happy path); new `tests/test_files_api.py`
cases for the image/config session gate, and an explicit test that script
GETs remain ungated.

**What this buys:** it closes the gap where a third party can `GET` a
config/image file cold, without ever establishing a session, and it stops
casual interference with an already-claimed session. It does **not** stop
the first-claim-wins race itself (whoever calls `provision-request` first
still sets the pinned identity), and MAC/IP are only as trustworthy as the
network underneath — an attacker willing to spoof a MAC and ARP-spoof to
match an IP defeats this the same way they'd defeat anything else here. See
[docs/security-faq.md](docs/security-faq.md) for the full reasoning.

**No Kea-side changes needed for this to be reliable.** Pinning only has to
hold for the lifetime of a single `ProvisioningSession` — from
`provision-request` until it's completed/deleted — not across reboots or
separate attempts. A device doesn't renegotiate its lease mid-flow under
normal operation, and standard DHCP behavior (Kea included) already returns
a MAC its existing lease's IP as long as that lease hasn't expired —
allocator choice (iterative/random) only matters for a client with no
current lease at all. A static MAC→IP mapping (Kea host reservations, or
switching DHCP servers for a MAC-hash allocator) was considered and
dropped: it requires knowing a device's MAC before first boot, which isn't
available for Classic ZTP hardware (unlike the serial, a MAC isn't visible
until the device is powered on and connected — at which point you've
already lost the zero-touch property), and it wasn't solving a real gap
anyway, since a new attempt after a session ends is expected to pin a fresh
identity rather than match the previous one.

Security value here still depends on DHCP snooping + dynamic ARP
inspection at the switch (see [deployment.md](docs/deployment.md),
"Network isolation — strongly recommended") to make MAC/IP spoofing costly
in the first place — this item is about closing the cold-fetch/interference
gap, not about strengthening MAC/IP as an identity.

## 8. Containerization validation (Quadlet/Podman)

Both [alpha.md](alpha.md) and [docs/deployment.md](docs/deployment.md) mark
the Quadlet unit and Podman credential-forwarding behavior (`LoadCredential=`
→ `$CREDENTIALS_DIRECTORY`) as "out of scope for alpha sign-off" and
"confirm this against the Podman version actually in use when this gets
validated in the containerization phase." Alpha's `quadlet/drawbridge.container`
was written but never run end-to-end. Beta should actually deploy via the
Quadlet unit and confirm credential forwarding works as documented (or wire
the bind-mount fallback for older Podman versions) before relying on the
"systemd credential doesn't force a password reset" guarantee in
[docs/authentication.md](docs/authentication.md).

**Correction found during implementation planning:** `quadlet/drawbridge.container`
does not actually exist anywhere in the repo or git history, despite being
referenced as if it already does in `architecture.md`'s repo-layout tree,
`deployment.md`, and this file. This item is bigger than "validate an
existing unit" — it needs to be **authored from scratch**, then validated.
Confirmed in scope as such.

**Correction found during implementation:** `/srv/drawbridge/{data,files}`
requires root to create and `chown` — wrong for a rootless Podman unit,
which runs as the invoking user. Volume mounts live under that user's own
XDG data dir instead (`~/.local/share/drawbridge/{data,files}`, `%h` in the
Quadlet unit), no `sudo`/`chown` needed.

**Implementation plan:** depends on the TLS section above (cert bind mount)
and the ZTP client logging section above (rsyslog port/mount). Author
`quadlet/drawbridge.container`: image ref, port mappings (app port +
`514/udp` for rsyslog), volume mounts for `~/.local/share/drawbridge/{data,files}` +
the new TLS cert directory, `[Service] LoadCredential=admin_password:...`.
Actually run it against the installed Podman version; confirm or fix
`CREDENTIALS_DIRECTORY` forwarding; update `deployment.md` to remove its
current hedging language once confirmed one way or the other. No CI
smoke-test step added for this — manual validation stays acceptable, per
this section's own existing text; an automated Quadlet-deploy check in
`.github/workflows/ci.yml` is an optional stretch, not required for beta.