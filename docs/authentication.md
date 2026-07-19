# Authentication

Drawbridge's management UI/API (device allowlist CRUD, script management,
user management) requires an authenticated operator session. Devices are
unaffected — `/api/provision-request`, `/api/provision-complete`, and the
device-facing `/scripts/<filename>` fetch stay unauthenticated, gated only by
network isolation and the serial lookup itself, per the threat model in
[architecture.md](architecture.md).

**Flask-Login** owns session/identity (`LoginManager`, `current_user`,
`@login_required`, `UserMixin` on the `User` model — see
[database.md](database.md)). It was chosen over a batteries-included
extension like Flask-Security-Too because it has no opinion about *how* a
user is authenticated — it only tracks who is currently logged in. That
separation matters here: local logins call `check_password_hash()` against
`User.password_hash` and then `login_user()`; a SAML login validates the IdP
assertion (`drawbridge/saml.py`) and then calls the same `login_user()`.
Passwords are
hashed with Werkzeug's `generate_password_hash`/`check_password_hash`
(already a Flask dependency — no new password library needed).

Local and SAML authentication are intended to coexist indefinitely, not
SAML-replaces-local — `User.auth_source` distinguishes them and
`password_hash` stays nullable for SAML-only accounts.

## User management and account lifecycle

Two roles: `admin` and `operator`. Operators can do everything except manage
other accounts — they have no access to `/api/users`. Only admins can create
accounts, change roles, or delete accounts.

**First admin** — bootstrapped on first startup (see
[decisions.md](decisions.md) / `alpha.md`): username `admin`, password
sourced per "Bootstrap admin password sources" below.

### Bootstrap admin password sources

`drawbridge/db.py`'s `_initial_admin_password()` resolves the bootstrap
admin's password in this priority order, each seeded only on first run (the
missing-DB-file gate — same as the rest of `_bootstrap_once`). The line
between forcing a reset and not isn't "who chose the password" — it's
whether the plaintext ends up somewhere durable and outside the app's
control:

1. **A systemd credential** named `admin_password` — read from
   `$CREDENTIALS_DIRECTORY/admin_password` when `LoadCredential=` (or
   `SetCredential=`) supplies one (see [deployment.md](deployment.md)). The
   only source that doesn't force a reset: the plaintext is exposed to the
   process only via a private, per-invocation directory, and never appears
   in `podman inspect`, `/proc/*/environ`, `systemctl show`, or any log.
2. **`ADMIN_PASSWORD`**, a plaintext env var. This value has to persist at
   rest somewhere (a Quadlet unit's `Environment=` line, a `.env` file) for
   the container to read it on every restart, so it's a standing plaintext
   credential for as long as that file exists. An empty or unset
   `ADMIN_PASSWORD` falls through to the next source, not to an empty
   password.
3. **The default**: a random 12-character password (`secrets.choice`),
   printed to stdout once. High entropy doesn't help here — the risk isn't
   guessing, it's that container stdout routinely ends up retained
   indefinitely in journald or shipped to log-aggregation infrastructure
   with broader read access than a single config file. The password itself
   is only ever shown this once, but the log line containing it typically
   outlives that.

Sources 2 and 3 both set `User.must_reset_password` on the bootstrap admin
to bound that exposure window — `login()` validates the password normally
but withholds a session (`POST /api/v1/auth/login` returns
`{must_reset_password: true, username}` instead of a logged-in session)
until `POST /api/v1/auth/reset-password` (`username`, `current_password`,
`new_password`) is called, which sets a new password, clears the flag, and
establishes the session itself.

`reset-password` is deliberately a separate, unauthenticated-by-necessity
route rather than an extension of `/auth/change-password`: it's the only
way to complete a reset when no session exists yet, and gating it on
`must_reset_password` being set means it can't be used as a back door
around the normal, session-based change-password flow for accounts that
don't need one.

**Subsequent accounts** — an admin creates a user with just a username and a
role via `POST /api/users`. No password is set at creation time
(`password_hash` stays `NULL`, `auth_source='local'`), and a single-use
`claim_token` (`secrets.token_urlsafe(32)`) is generated and returned once in
that response's payload — never shown again, never included when listing
users (`GET /api/users`). The account is unusable via `/login` in this state
— `login()` treats a null `password_hash` as invalid credentials for both
this case and SAML-only accounts, so the two must not be conflated.

`POST /api/auth/claim` requires the matching `token` alongside the username
and new password; on success `claim_token` is nulled so the token can't be
reused. This replaces alpha's passwordless-until-claimed race (first `POST`
with a known username won) — that race is now closed: claiming requires
something out-of-band (the token, handed to the intended user by the admin
who created the account), not just guessing or racing on the username.

Once claimed, a user changes their own password later via
`POST /api/auth/change-password` (requires their current password). An admin
can also force a re-claim via `POST /api/users/<id>/reset-password`: it nulls
`password_hash` and issues a fresh `claim_token`, returned the same way as at
creation, and the account goes through `POST /api/auth/claim` again — the
same path a brand-new account uses. This intentionally does **not** reuse
`POST /api/auth/reset-password`: that route checks the caller's
*current_password* against `password_hash`, which is impossible to satisfy
once an admin has nulled it. `must_reset_password` is left untouched by this
route, since `login()` already refuses a null `password_hash` before ever
consulting that flag. Only local accounts can be reset this way (400 for
SAML accounts, which have no local password to reset).

All three password-entry endpoints (`claim`, `reset-password`,
`change-password`) enforce a minimum password length of 8 characters.
`generate_password_hash` calls are pinned to `method='scrypt'` explicitly, so
a future Werkzeug default change can't silently alter hash strength for new
passwords. Every failed attempt on `login`/`claim`/`reset-password`/
`change-password` logs a single line (username + event type, e.g.
`login_failed`, `claim_failed`, `weak_password`) — never the password
itself — so repeated failures are visible in logs even though the client
response stays generic. `POST /api/v1/auth/login`, `/claim`, and
`/reset-password` are also rate-limited per-IP (`Flask-Limiter`, in-memory
storage — correctly scoped for a single-worker/SQLite deployment; under
multiple Gunicorn workers the effective limit is per-worker, not global).

`login()`/`reset_password()` also check `login_user()`'s return value: if
`User.is_active` were ever `False` (nothing in the app sets this today — no
deactivate-account control exists yet), Flask-Login refuses to establish a
session, and the route now correctly returns `403 account_inactive` instead
of a false "logged in" response.

**Deletion** — an admin can delete any other account via `DELETE
/api/users/<id>` without needing that user's password. Deleting or demoting
(via `PUT /api/users/<id>`) the last remaining admin is rejected, so the
system can never end up with zero admins.

## Auth backends: local, SAML, and how a future method plugs in

Authentication logic is split by *how credentials are shaped*, not lumped
into the route handlers:

- **Credential-submission methods** (a username+password POSTed to
  Drawbridge) go through `drawbridge/auth_backends.py`'s
  `CREDENTIAL_BACKENDS` registry, keyed on `User.auth_source`. `login()`
  doesn't know a user's `auth_source` until after the DB lookup, so this
  dict is what makes that dispatch pluggable:
  ```python
  backend = CREDENTIAL_BACKENDS.get(user.auth_source) if user else None
  if not backend or not backend.verify_login(user, password):
      ...  # 401 — unknown user, wrong password, and "this account uses a
           # different method" are all indistinguishable to the caller
  ```
  `LocalAuthBackend` (checking `password_hash` via `check_password_hash`) is
  the only entry today. A future LDAP backend (bind against an LDAP server
  instead of comparing a local hash) registers as `CREDENTIAL_BACKENDS['ldap']`
  and `login()` itself doesn't change. `claim`/`reset-password`/
  `change-password` stay local-only *by feature* (a SAML/LDAP account has no
  local password to claim/reset/change) — they call `LocalAuthBackend`
  directly rather than through the registry, since they always mean "the
  local method," never a dynamic dispatch.
- **SAML** is structurally different — a browser redirect to the IdP and an
  IdP-POSTed assertion, not a credential submitted to Drawbridge — so it
  isn't part of `CREDENTIAL_BACKENDS`. It has its own class
  (`drawbridge/saml.py`'s `SamlAuthBackend`) and its own routes.

Auth-method-specific data stays as flat nullable columns on `User`
(`password_hash`/`claim_token` for local; `saml_issuer`/`saml_subject` for
SAML) rather than separate per-method tables — cheap for a small number of
nullable columns per method, and avoids a join on every login. `auth_source`
is a plain string, not a DB-level enum, so adding a value for a new method
needs no migration.

## SAML SP integration

**Status: implemented** (generic SP side — validates assertions from
whatever IdP the operator configures, no bundled/preferred IdP).
`python3-saml` (OneLogin's toolkit) handles SP metadata, the `AuthnRequest`,
and assertion validation; there's no Flask-specific SAML extension needed on
top of Flask-Login — a successful assertion just calls the same
`login_user()` a local login does.

Routes, all in `drawbridge/api/auth.py` under a top-level `/saml` prefix
(not `{API_PREFIX}/auth` — these URLs are registered by hand with the IdP,
not versioned the way the rest of the API is):
- `GET /saml/metadata` — SP metadata XML for IdP configuration.
- `GET /saml/login` — redirects to the IdP's SSO endpoint.
- `POST /saml/acs` — Assertion Consumer Service. Validates the assertion
  (`SamlAuthBackend.process_acs`), then `queries.get_or_create_saml_user()`
  upserts a `User` keyed on `saml_issuer`+`saml_subject` (self-provisioned
  on first login, defaulting to `role='operator'` — SAML carries no
  group-to-role mapping in this release), and calls `login_user()`.

All three routes return `404 saml_disabled` unless `SAML_SETTINGS_PATH`
(default `/app/data/saml`) contains a `settings.json` — see
[deployment.md](deployment.md), "SAML SSO". SP certificate/key and IdP
metadata are config, mounted the same way `/app/scripts` is mounted, not
hardcoded.
