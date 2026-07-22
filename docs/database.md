# Database

Managed via SQLAlchemy ORM models in `drawbridge/models.py`. `drawbridge/db.py`
owns the `Engine`/`sessionmaker` and an `init_db(app)` that calls
`Base.metadata.create_all()` on first run. `DATABASE_PATH` is a bare
filesystem path by default (SQLite), or a full SQLAlchemy URL such as
`postgresql+psycopg://user:pass@host/dbname` to use PostgreSQL instead — see
"Concurrency under multiple Gunicorn workers" below for what that choice
affects.

## Schema

```python
class Device(Base):
    """Operator-managed allowlist entry. Exists from registration through the
    full ZTP lifecycle — not deleted on provisioning so a failed run can be
    retried without re-registration. Operator must explicitly DELETE to remove."""
    __tablename__ = 'devices'

    serial: Mapped[str] = mapped_column(primary_key=True)
    mac: Mapped[str | None]
    description: Mapped[str | None]
    image: Mapped[str | None]        # falls back to default_image Setting on creation
    config_file: Mapped[str | None]  # falls back to default_config_file Setting on creation
    script: Mapped[str | None]       # falls back to default_script Setting on creation
    added_at: Mapped[str]
    added_by: Mapped[str | None]


class ProvisioningSession(Base):
    """Transient record of an in-progress ZTP run. Created when
    /api/provision-request approves a serial; deleted when
    /api/provision-complete fires (success or failure), or when an operator
    cancels a stale session (is_stale(), see below). The Device allowlist
    row is not touched."""
    __tablename__ = 'provisioning_sessions'

    serial: Mapped[str] = mapped_column(primary_key=True)
    mac: Mapped[str | None]
    ip: Mapped[str | None]
    image: Mapped[str | None]        # copied from the Device row's assignment at approval time
    config_file: Mapped[str | None]  # copied from the Device row's assignment at approval time
    state: Mapped[str]               # 'lease_approved', 'script_fetched', 'downloading',
                                     # 'updating_software', 'rebooting', 'configuring', 'error'
    approved_at: Mapped[str]
    last_seen_at: Mapped[str]        # bumped on every repeat provision-request and every
                                     # correlated /device-logs POST — see "Stale sessions" below


class ProvisioningLog(Base):
    """Archival record written when a device completes or fails provisioning
    and its ProvisioningSession row is deleted. Subject to the retention
    policy in Setting; purged once a row outlives it."""
    __tablename__ = 'provisioning_log'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    serial: Mapped[str]
    event: Mapped[str]               # 'provision_complete', 'provision_failed'
    image: Mapped[str | None]
    config_file: Mapped[str | None]
    ip: Mapped[str | None]
    timestamp: Mapped[str]
    detail: Mapped[str | None]


class ZTPFile(Base):
    """Metadata record for every file managed by Drawbridge. The file itself
    lives on disk under FILES_PATH/<type>/; this row tracks its type, size,
    and SHA-256 so the UI and devices can verify integrity. Composite PK on
    (file_type, filename) — the same filename may exist under different types."""
    __tablename__ = 'ztp_files'

    file_type:   Mapped[str] = mapped_column(primary_key=True)  # 'image', 'config', 'script'
    filename:    Mapped[str] = mapped_column(primary_key=True)
    size_bytes:  Mapped[int]
    sha256:      Mapped[str]
    uploaded_at: Mapped[str]
    uploaded_by: Mapped[str | None]


class Setting(Base):
    """Small admin-configurable key/value store. First row of interest:
    key='log_retention_days', value='30' (or 'indefinite')."""
    __tablename__ = 'settings'

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    updated_at: Mapped[str]
    updated_by: Mapped[str | None]


class User(Base, UserMixin):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str | None]
    password_hash: Mapped[str | None]   # null for SAML-only operators
    role: Mapped[str]                   # 'admin' or 'operator'
    auth_source: Mapped[str]            # 'local' or 'saml'
    saml_issuer: Mapped[str | None]     # IdP entity ID, set once SAML lands
    saml_subject: Mapped[str | None]    # IdP NameID, set once SAML lands
    is_active: Mapped[bool] = mapped_column(default=True)
    must_reset_password: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str]
    last_login_at: Mapped[str | None]
```

`auth_source`, `saml_issuer`, and `saml_subject` exist now, ahead of the SAML
work, so that adding SAML later (see [authentication.md](authentication.md))
is additive — no migration to widen the `users` table when it happens.

`must_reset_password` is set on the bootstrap admin only when its initial
password came from `ADMIN_PASSWORD` (a plaintext env var) rather than a
systemd credential or the default random-generated password — see
[authentication.md](authentication.md) ("Bootstrap admin password sources")
for why those three sources aren't treated the same.

A request-scoped session is opened per Flask request (e.g. via
`app.teardown_appcontext`) and closed/rolled back at the end of the request.

## Stale sessions

A `ProvisioningSession` only ever gets deleted by the device itself calling
`/api/v1/provision-complete` — nothing times it out automatically. A device
that crashes, loses power, or is physically pulled mid-install leaves its
session (and therefore its `devices` allowlist entry, since `DELETE
/devices/<serial>` refuses to remove a device with an active session)
permanently stuck with no self-healing path.

Drawbridge deliberately does **not** auto-expire sessions in the
background. `ProvisioningSession.is_stale()` (`SESSION_STALE_AFTER_MINUTES`,
default 60) is only ever used to *gate* an operator-initiated cancel
(`DELETE /api/v1/devices/sessions/<serial>`, `409 session_not_stale` while
still within the window) — never to delete a session on its own. Silently
reaping a session that's still genuinely active (a false positive on the
timeout) would be worse than the stuck-device annoyance it fixes: the
device's own `/provision-complete` call would then 404
(`device_not_active`), and Drawbridge would have no `ProvisioningLog` row
for what actually happened, since that write only happens inside the
handler that just rejected the call. An operator-initiated cancel writes a
`ProvisioningLog` row (`event='provision_cancelled'`) before deleting the
session, so the outcome is never lost the way a silent auto-expiry's would
be.

A cancelled session's `DeviceLogEntry` rows are **not** cleared — treated
the same as a failure, not a success (see "Log Retention & Data
Minimisation" below): those are exactly the logs an operator investigating
"why did this get stuck" would want.

## Schema evolution

There's no migration framework (no Alembic, no versioned migration
scripts) — `init_db()` calls `Base.metadata.create_all()`, which creates
whatever tables don't exist yet and stops there. That's sufficient for new
tables, but SQLAlchemy's `create_all()` does *not* retroactively add a
newly-declared index to a table that already exists — an already-deployed
install would silently never get it. `drawbridge/db.py`'s `_sync_indexes()`
closes that specific gap: right after `create_all()`, inside the same
cross-process bootstrap lock, it explicitly (re)creates every index
declared on the models with `checkfirst=True`, so an upgrade picks up
index changes without needing a fresh install. This covers index
additions only — `DeviceLogEntry.timestamp` and `ProvisioningLog.timestamp`
are both indexed today, supporting the lazy-purge queries below, which
would otherwise be full table scans on every single insert. Column
additions/type changes are a different, harder problem this doesn't
attempt to solve; none exist yet.

## Concurrency under multiple Gunicorn workers

Drawbridge supports two database backends (`DATABASE_PATH`, see above).
Which one is configured decides how multi-worker concurrency is handled —
see [decisions.md](decisions.md) for why SQLite is forced single-worker
rather than the whole app being migrated to Postgres.

### SQLite (default)

`gunicorn.conf.py` forces `workers = 1` whenever `DATABASE_PATH` resolves to
SQLite. This is what makes a single worker process the actual, enforced
multi-*worker* concurrency story for SQLite — not WAL or `busy_timeout`,
which only matter for the greenlet-level concurrency *within* that one
process:

- `PRAGMA journal_mode=WAL` is set on every new connection (a SQLAlchemy
  `connect` event listener in `drawbridge/db.py`). WAL lets readers proceed
  without blocking on the one in-progress writer — relevant because the
  `gevent` worker class runs many greenlets inside that single process, all
  opening connections to the same `drawbridge.db` file.
- `PRAGMA busy_timeout=<SQLITE_BUSY_TIMEOUT_MS>` is set on every connection
  so a greenlet retries internally for a short window instead of raising
  `database is locked` immediately when another greenlet in the same
  process holds the write lock. Default is `1000`ms.
- The SQLAlchemy `Engine` is created inside `create_app()`, i.e. after
  Gunicorn forks the worker — not at module import time. A `sqlite3`
  connection shared across a `fork()` corrupts the database. `connect_args`
  includes `check_same_thread=False` since gevent can hand a checked-out
  connection between greenlets within the one process.
- WAL mode requires the database file on a local filesystem — true here
  (bind-mounted host directory on the same Ubuntu host), not network
  storage. WAL also produces `drawbridge.db-wal` and `drawbridge.db-shm`
  alongside the main file; any backup tooling must capture all three, not
  just `drawbridge.db`.
- First-run schema creation + seeding + admin bootstrap is serialized by an
  `fcntl.flock`-based advisory lock (`drawbridge/db.py`'s `_bootstrap_once`)
  on a `DATABASE_PATH + '.bootstrap-lock'` sidecar file. With `workers = 1`
  enforced, only one process is ever inside it — the lock is unconditionally
  sufficient, not a race mitigation. (This replaces an earlier, genuinely
  reproducing Gunicorn multi-worker bootstrap race — see
  [kea-hook-findings.md](kea-hook-findings.md) #5.)

### PostgreSQL (opt-in)

Setting `DATABASE_PATH` to a `postgresql+psycopg://...` URL allows multiple
workers (`WORKERS` env var, default `4`) — ordinary write concurrency is
Postgres's normal MVCC/row-locking, so none of the SQLite-specific pragmas
above apply (`_create_engine()` skips them for any non-SQLite dialect). The
same first-run critical section is serialized with a `pg_advisory_lock`/
`pg_advisory_unlock` pair instead of a file lock — same structure as the
SQLite path, a different underlying primitive.

**Coverage note:** the Postgres branch is structurally parallel to the
tested SQLite path but isn't itself exercised by the test suite (no live
Postgres server in CI) — see [testing.md](testing.md).

## Log Retention & Data Minimisation

Drawbridge is not an inventory system in spirit, though the `Device`
allowlist row itself persists until an operator explicitly deletes it (see
the `Device` docstring above). What's transient is the `ProvisioningSession`,
deleted as soon as `/api/provision-complete` fires (see
[architecture.md](architecture.md), DHCP Flow) — or, for a stuck session,
via an operator's explicit cancel once it's stale (see "Stale sessions"
above). What persists past that point is `ProvisioningLog`: when a device
was provisioned and what image/config file it received, for audit and
troubleshooting, not asset tracking.

- Retention is controlled by the `Setting` row keyed `log_retention_days` —
  an **admin-configurable, DB-backed setting** via `GET`/`PUT
  /api/settings/log-retention`, not a fixed env var, so it can change
  without a container restart.
- Default is `30` days. `LOG_RETENTION_DAYS` (env var) seeds this row on
  first `init_db()` run only — after that, the DB value is authoritative.
- An admin can set the value to the literal string `indefinite`, which
  disables purging entirely. This is an explicit, visible override of the
  "don't keep this stuff" default, not a loophole — the schema and the UI
  should make clear that's what's happening.
- Purging is lazy, not a separate scheduled job: before inserting a new
  `ProvisioningLog` row, delete existing rows older than the current
  retention setting (skipped entirely when retention is `indefinite`). This
  needs no extra process or systemd timer, consistent with the project's
  preference for minimal moving parts — the tradeoff is that on a
  long-idle deployment, expired rows linger until the next provisioning
  event, which is acceptable for a log, not a security control.
- `provision_complete`, `provision_failed`, and `provision_cancelled`
  (an operator cancelling a stale session — see "Stale sessions" above)
  events land in `ProvisioningLog` — lease decisions are not logged. The
  retention rule covers all device-identifying archival data.
- `DeviceLogEntry` (see [logging.md](logging.md)) shares the same
  `log_retention_days` setting — not a second, independently configured
  retention knob — but isn't purely time-based like `ProvisioningLog`:
  a device's raw log stream is only useful for watching that specific run
  in progress, so on a **successful** `provision_complete`
  (`drawbridge/api/leases.py`) its `DeviceLogEntry` rows are deleted
  immediately, regardless of `log_retention_days`. A **failed** or
  **cancelled** run's rows are left in place — still troubleshooting-relevant
  — and age out via the same lazy-purge-on-insert pattern as everything
  else, until a later successful completion for that serial clears them,
  `log_retention_days` catches up with them first, or the device is removed
  from the allowlist entirely (`DELETE /api/v1/devices/<serial>` also clears
  that serial's `DeviceLogEntry` rows unconditionally — a device that's no
  longer allowlisted has nothing left for Drawbridge to track except its
  `ProvisioningLog` history).
