# Alpha Implementation Plan

## Scope

Alpha is "done" when:
- The full backend (DB, auth, all API blueprints) is implemented and tested per
  [docs/testing.md](docs/testing.md), runnable via `flask run` against SQLite —
  no container required.
- `/api/provision-request` is fully implemented and verified via direct HTTP
  calls (pytest + curl/Postman simulating what the ZTP script's phone-home
  call would send), not against a live Kea instance. The `kea/` configs are
  vanilla (no custom hook, no host reservations — see
  [docs/decisions.md](docs/decisions.md)) and written as code so they're
  ready to test against real Kea later, but a working Kea box is not a gate
  for alpha sign-off.
- A minimal Vue admin UI exists: login, device list/add/remove, provisioning
  log view. Users and settings management can be bare-bones (a working form is
  enough; polish is a follow-up).
- Auth is local username/password only via Flask-Login. SAML fields stay on
  the `User` model (already documented) but SP integration is not built.
- Containerfile/Quadlet are out of scope for alpha sign-off — validated in a
  later phase.

Everything below follows the schema, contracts, and decisions already
recorded in `docs/`. This plan does not redesign anything — it sequences the
implementation.

## Current state (as of this plan)

Implemented: `drawbridge/main.py` (app factory, `/health`, static-serving
catch-all route), `drawbridge/utils.py` (response envelopes, file hashing),
`tests/conftest.py` (app/client fixtures), frontend scaffolding (Vite config,
placeholder `App.vue`).

Not yet implemented: all `kea/*` config files and the hook callout, all real
frontend views. Test coverage per [docs/testing.md](docs/testing.md)'s
checklist (step 7) is still in progress.

## Step-by-step

### 1. Database layer (DONE)
- `drawbridge/models.py`: `Device`, `ProvisioningLog`, `Setting`, `User`
  exactly per [docs/database.md](docs/database.md) schema.
- `drawbridge/db.py`: SQLAlchemy `Engine`/`sessionmaker`, `init_db(app)`
  (`Base.metadata.create_all()` + seed `Setting(log_retention_days)` from
  `LOG_RETENTION_DAYS` env var on first run), WAL + `busy_timeout` connect
  event listener, request-scoped session via `app.teardown_appcontext`.
- Wire `init_db(app)` into `create_app()` in `main.py` (the TODO already
  marks where).
- Engine must be created inside `create_app()`, not at module import time
  (post-fork safety under Gunicorn — see decisions.md).

### 2. Auth foundation (DONE)
- `drawbridge/auth.py`: `LoginManager` setup, `user_loader`, password hashing
  via Werkzeug (`generate_password_hash`/`check_password_hash`).
- **First-admin bootstrap**, triggered from `init_db(app)` in `db.py`: check
  whether `DATABASE_PATH` exists on disk *before* `create_all()` runs. If it
  doesn't (first run), after creating the schema, insert
  `User(username='admin', role='admin', auth_source='local')` with a
  randomly generated 12-character password (`secrets.choice` over
  letters+digits — cryptographically random, not `random`), hash it with
  Werkzeug, and print the plaintext password to stdout once:
  `Drawbridge: created initial admin user 'admin', password: <pw> — record this now, it will not be shown again.`
  This only ever fires on the missing-DB-file path, never on subsequent
  starts against an existing DB.

### 3. Kea integration (SUPERSEDED — not built)
A Kea Control Agent client (`reservation-add`/`reservation-del`) was
planned here, driving a DHCP-level allow/deny gate. Abandoned — see
[docs/decisions.md](docs/decisions.md) ("Provisioning gate moved from
DHCP-level to script-level") and [docs/kea-hook-findings.md](docs/kea-hook-findings.md).
Kea now runs a vanilla config with no Drawbridge-facing API calls at all;
the gate lives in `scripts/ztp-base.py`'s phone-home call instead (step 5).

### 4. API blueprints (`drawbridge/api/`)
Build and register in this order, each with its tests immediately after
(per [docs/testing.md](docs/testing.md)) rather than batching all routes
before any tests:

1. (DONE) `auth.py` — `POST /api/auth/login`, `POST /api/auth/logout`,
   `GET /api/auth/me`.
2. (DONE) `devices.py` — `GET/POST /api/devices`, `GET/DELETE /api/devices/<serial>`.
   POST is idempotent on re-registering the same serial.
3. (DONE) `leases.py` — `GET /api/provision-request`. The core security gate,
   called by the ZTP script's phone-home step, not Kea: known serial → 200
   + `ProvisioningSession` created; unknown → 404; missing serial → 422.
   `PUT/POST /api/provision-complete` (device reports outcome; deletes the
   `ProvisioningSession` row — the `devices` allowlist row is untouched —
   writes `ProvisioningLog`).
4. (DONE) `files.py` — `GET /scripts/<filename>`, `GET /images/<filename>`, `GET /configs/<filename>` (unauthenticated device-facing
   fetch), the authenticated management variant for file upload/listing.
5. (DONE) `settings.py` — merges what would have been a separate `users.py`:
   admin-only CRUD for operator accounts (`GET/POST /api/users`,
   `PUT/DELETE /api/users/<id>`) alongside `GET/PUT
   /api/settings/log-retention` (admin-only on PUT). Wire the
   lazy-purge-on-insert logic here and into the `ProvisioningLog` insert path
   used by `leases.py`/`files.py`. Last-remaining-admin deletes/demotions are
   rejected. See [docs/authentication.md](docs/authentication.md) for the
   account lifecycle (admin creates username+role only, passwordless until
   the user claims it via `/api/auth/claim`). The password-claim and
   self-service change-password routes live in `auth.py` instead, since they
   act on the caller's own identity rather than admin CRUD on other
   accounts — `POST /api/auth/claim`, `POST /api/auth/change-password`.

Register all blueprints in `create_app()` (the TODO already marks where) and
apply `@login_required` per the matrix in [docs/api.md](docs/api.md) — only
`/api/provision-request`, `/api/provision-complete`, and the device-facing
`/scripts/<filename>` stay open.

### 5. Base ZTP script (DONE)
- `scripts/ztp-base.py`: a stub for alpha, not a real provisioning script.
  Its first action is the phone-home gate: `GET /api/provision-request` with
  its own serial (read via `show version`), exiting cleanly without
  reporting anything further if denied or unreachable. Beyond that it
  exercises the serve/fetch/callback contract end-to-end for testing — it
  emulates what a device would request and PUT back, nothing more. Real
  Day-0 IOS XE provisioning logic (cert validation, hash verification,
  actual config push) is deliberately deferred to a later phase once this
  is tested against real hardware.
- Keep it to stdlib only (`urllib`/`http.client`/`ssl`, no `requests` or
  other third-party imports, no f-strings or other syntax assumptions) —
  IOS XE's onboard Python environment (Guestshell) is restrictive, and
  whatever gets written now should not need a rewrite later just to run
  there. Procedural, not class-based.

### 6. Kea-side artifacts (DONE — vanilla config, no hook)
- `kea/kea-dhcp4.conf`, `kea/kea-ctrl-agent.conf` per
  [docs/kea.md](docs/kea.md) — dynamic pool, Option 67 unconditional for
  every client, no `hooks-libraries`, no `host_cmds`, no `hosts-database`.
- No native hook/callout — see [docs/decisions.md](docs/decisions.md) for
  why that approach was abandoned. The gate is entirely in
  `scripts/ztp-base.py` (step 5) and `/api/provision-request` (step 4).
- Not gated by a live Kea instance for alpha sign-off, but should be
  internally consistent with the `/api/provision-request` contract.

### 7. Test suite (DONE)
Fill out `tests/` per [docs/testing.md](docs/testing.md)'s checklist:
`test_lease_api.py`, `test_ztp_base.py`, `test_devices_api.py`,
`test_auth_api.py`, plus users/settings/provisioning-log coverage. No Kea
mocking needed — nothing in the request path calls Kea. Include the
multi-worker-style concurrent-write test.

### 8. Minimal frontend
Stack: Vue Router for navigation, **Pinia** for state, **axios** for HTTP.
API access is centralized in Pinia stores, not components — components call
store actions and read store state; they never import axios directly. This
keeps each domain's request/error/loading handling in one place regardless
of where in the tree a component sits.

- `frontend/src/api/client.js` — single configured axios instance
  (`withCredentials: true` for session cookies, a response interceptor that
  normalizes the `{success, message, payload/error}` envelope from
  `drawbridge/utils.py` and redirects to `/login` on a 401).
- `frontend/src/stores/` — one Pinia store per domain, each owning its own
  axios calls against `client.js`:
  - `auth.js` — `login()`, `logout()`, `fetchMe()`, `claim()`,
    `changePassword()`, holds `currentUser`.
  - `devices.js` — `list()`, `add()`, `remove()`.
  - `log.js` — `fetchLog()`.
  - `users.js`, `settings.js` — bare-bones CRUD/get-set, admin-only. Kept as
    separate stores (one per domain) even though their views are merged.
- Views: Login, Devices (list/add/remove), Log, Settings — each thin,
  delegating to its store(s). Settings hosts both the log-retention form and
  admin-only user management (list/create/change-role/delete) in one page,
  backed by the separate `users.js`/`settings.js` stores.
- Vue Router so the catch-all `index.html` fallback in `main.py` has routes
  to resolve on a hard refresh; a navigation guard redirects to `/login`
  when `auth.currentUser` is unset.
- Manual pass through `dev.sh` (Flask + Vite dev server) to confirm
  the golden path: log in as the bootstrapped admin, register a device,
  simulate a provision-request via curl, confirm it shows in the log, remove
  the device.

### 9. End-to-end manual verification (no live Kea)
- `pytest` green.
- `flask run` + curl sequence simulating the full flow against
  `/api/provision-request` and `/api/provision-complete` by hand, confirming
  DB state transitions (`ProvisioningSession` created → deleted,
  `ProvisioningLog` rows appended, `devices` row untouched) match
  [docs/architecture.md](docs/architecture.md)'s DHCP Flow.
- UI smoke test per step 8.

## Resolved decisions

1. **First admin bootstrap** — see step 2. Triggered by absence of the DB
   file at `DATABASE_PATH`, not an env var or CLI command. Username
   `admin`, password is a random 12-character string printed to stdout
   once, never persisted in plaintext.
2. **`scripts/ztp-base.py`** — stub only for alpha (see step 5). Real
   on-device provisioning logic is a later phase, scoped once tested
   against actual IOS XE hardware.
3. **Frontend stack** — Vue Router + Pinia + axios, with API calls
   centralized in Pinia stores rather than components (see step 8).
4. **`tests/dev-data`** — holding directory for the SQLite file
   `dev.sh` creates during a live dev session; reused as the
   location for test-suite fixtures/seed data once the test suite is built
   in step 7.
