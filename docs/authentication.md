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
`User.password_hash` and then `login_user()`; a future SAML login validates
the IdP assertion and then calls the same `login_user()`. Passwords are
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
(`password_hash` stays `NULL`, `auth_source='local'`). The account is
unusable via `/login` in this state — `login()` treats a null
`password_hash` as invalid credentials for both this case and SAML-only
accounts, so the two must not be conflated.

The first person to `POST /api/auth/claim` with that username and a new
password sets it, filling in `password_hash`. This is deliberately
passwordless-until-claimed rather than a temp-password/token flow: simpler,
and accepted as a known tradeoff — since only admins create accounts on an
internal, network-isolated deployment, the account-takeover race (someone
else claiming the username first) is treated as low risk for alpha. Revisit
if Drawbridge's threat model changes (e.g. self-service signup, exposure
beyond the isolated network).

Once claimed, a user changes their own password later via
`POST /api/auth/change-password` (requires their current password). There is
no separate admin-triggered password reset in alpha — an admin who needs to
let a user re-claim their account deletes and recreates it.

**Deletion** — an admin can delete any other account via `DELETE
/api/users/<id>` without needing that user's password. Deleting or demoting
(via `PUT /api/users/<id>`) the last remaining admin is rejected, so the
system can never end up with zero admins.

## Planned: SAML SP integration

Not implemented yet. When added:
- `python3-saml` (OneLogin's toolkit) will handle SP metadata, the
  `AuthnRequest`, and assertion validation — there's no need for a
  Flask-specific SAML extension on top of Flask-Login.
- New routes in `drawbridge/api/auth.py`: `GET /saml/metadata` (SP metadata
  for IdP configuration), `GET /saml/login` (redirect to IdP), `POST
  /saml/acs` (Assertion Consumer Service — validates the assertion, upserts
  the `User` row keyed on `saml_issuer`+`saml_subject`, calls `login_user()`).
- SP certificate/key and IdP metadata will be config, not hardcoded, mounted
  similarly to how `/app/scripts` is mounted today.
- `requirements.txt` will pick up `python3-saml` only once this is actually
  built, not speculatively now.
