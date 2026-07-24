import fcntl
import secrets
import string
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.security import generate_password_hash

from drawbridge.models import Base, Setting, User

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD_LENGTH = 12
ADMIN_CREDENTIAL_NAME = 'admin_password'

# Arbitrary but fixed pg_advisory_lock key for the bootstrap critical
# section below — any future second advisory lock should pick a different
# constant rather than collide with this one.
_BOOTSTRAP_LOCK_KEY = 835_217_004


def init_db(app):
    """Create this worker's Engine/sessionmaker and prepare the schema.

    Must be called inside create_app(), after Gunicorn forks each worker —
    never at module import time, since a sqlite3 connection shared across
    fork() corrupts the database (see docs/decisions.md).
    """
    engine = _create_engine(app)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.extensions['db_engine'] = engine
    app.extensions['db_session_factory'] = session_factory

    _bootstrap_once(app, engine, session_factory)

    app.teardown_appcontext(_close_session)


def _bootstrap_once(app, engine, session_factory):
    """Schema creation + seeding + admin bootstrap, serialized across
    processes so exactly one of them ever does the first-run work — see
    docs/kea-hook-findings.md #5 for the multi-worker race this replaces.
    The locking primitive differs by dialect; the sequence inside it
    doesn't. is_first_run is checked *inside* the lock, not before it —
    otherwise two Postgres workers could both observe an empty database
    before either has created it, recreating the same race on a different
    dialect.
    """
    if engine.dialect.name == 'sqlite':
        lock = _sqlite_lock(app.config['DATABASE_PATH'])
    else:
        lock = _postgres_lock(engine)

    with lock:
        is_first_run = _is_first_run(engine, app.config['DATABASE_PATH'])

        Base.metadata.create_all(engine)
        _sync_indexes(engine)

        with session_factory() as session:
            _seed_log_retention(session, app)
            _seed_default_image(session, app)
            _seed_default_config_file(session, app)
            if is_first_run:
                _bootstrap_admin(session, app)
            session.commit()


def _sync_indexes(engine):
    """create_all() only emits CREATE INDEX as part of creating a brand-new
    table — on an already-existing table (i.e. every upgrade of a
    previously-deployed install, not just a fresh one) an index= added to a
    model in a later version is silently skipped, since the table itself
    already exists and create_all() has nothing left to do for it. There's
    no migration framework here (see docs/database.md), so this is the one
    schema change that class of tool would otherwise handle: explicitly
    (re)creating every declared index with checkfirst=True closes that gap
    for index additions specifically. Column additions/type changes are a
    different, harder problem and aren't covered by this — none exist yet.
    Must run inside the same bootstrap lock as create_all() (see
    _bootstrap_once) so concurrent workers don't race CREATE INDEX.
    """
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)


@contextmanager
def _sqlite_lock(database_path: str):
    """fcntl.flock advisory lock on a sidecar file next to the database.
    The lock file's own existence/content carries no state — it's purely a
    mutex handle — so an operator deleting DATABASE_PATH to reset a dev
    database doesn't need to also delete the lock file; the next run will
    see the missing database and re-bootstrap normally.
    """
    lock_path = database_path + '.bootstrap-lock'
    with open(lock_path, 'w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


@contextmanager
def _postgres_lock(engine):
    """pg_advisory_lock/unlock on a dedicated connection, not the
    sessionmaker — the lock is scoped to one physical backend connection
    (not a transaction), so it must be taken and released on a connection
    that isn't handed back to the pool for other work in between.
    """
    with engine.connect() as connection:
        connection.execute(text('SELECT pg_advisory_lock(:key)'), {'key': _BOOTSTRAP_LOCK_KEY})
        try:
            yield
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': _BOOTSTRAP_LOCK_KEY})


def get_session() -> Session:
    """Request-scoped SQLAlchemy session, opened on first use within the
    current Flask app context and closed by the teardown callback that
    init_db() registers.
    """
    if 'db_session' not in g:
        g.db_session = current_app.extensions['db_session_factory']()
    return g.db_session


def _create_engine(app):
    url = _database_url(app.config['DATABASE_PATH'])
    connect_args = {}

    # check_same_thread=False: the gevent Gunicorn worker class can hand a
    # checked-out connection between greenlets within one process (see
    # docs/database.md, "Concurrency under multiple Gunicorn workers").
    # SQLite-only — meaningless for a client/server dialect.
    if url.startswith('sqlite'):
        connect_args['check_same_thread'] = False

    engine = create_engine(url, connect_args=connect_args)

    if engine.dialect.name == 'sqlite':
        busy_timeout_ms = app.config['SQLITE_BUSY_TIMEOUT_MS']

        @event.listens_for(engine, 'connect')
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute(f'PRAGMA busy_timeout={busy_timeout_ms}')
            cursor.close()

    return engine


def _is_sqlite(database_path: str) -> bool:
    """True for a bare filesystem path (the default) or an explicit
    sqlite:// URL; False for any other SQLAlchemy URL (e.g. a
    postgresql://... one). Same '://' sniff as _database_url(), factored
    out so gunicorn.conf.py can use it without duplicating the logic.
    """
    return '://' not in database_path or database_path.startswith('sqlite')


def _database_url(database_path: str) -> str:
    """DATABASE_PATH is a bare filesystem path by default, wrapped into a
    sqlite:/// URL here. A value that's already a SQLAlchemy URL (e.g.
    postgresql+psycopg://...) is passed through unchanged.
    """
    if '://' in database_path:
        return database_path
    return f'sqlite:///{database_path}'


def _is_first_run(engine, database_path: str) -> bool:
    """Whether this is the first time init_db has run against this
    database — gates the admin bootstrap. Must be called while holding the
    bootstrap lock (see _bootstrap_once) so concurrent workers don't all
    observe "empty" before any of them has created the schema.
    """
    if engine.dialect.name == 'sqlite':
        return not Path(database_path).exists()
    return not inspect(engine).has_table('users')


def _seed_log_retention(session, app):
    if session.get(Setting, 'log_retention_days') is None:
        session.add(Setting(
            key='log_retention_days',
            value=app.config['LOG_RETENTION_DAYS'],
        ))


def _seed_default_image(session, app):
    value = app.config['DEFAULT_IMAGE']
    if value is not None and session.get(Setting, 'default_image') is None:
        session.add(Setting(key='default_image', value=value))


def _seed_default_config_file(session, app):
    value = app.config['DEFAULT_CONFIG_FILE']
    if value is not None and session.get(Setting, 'default_config_file') is None:
        session.add(Setting(key='default_config_file', value=value))


def _initial_admin_password(app) -> tuple[str, bool, str]:
    """Resolves the bootstrap admin's initial password and whether it must
    be reset on first login, in priority order (see docs/authentication.md,
    "Bootstrap admin password sources"). The dividing line for forcing a
    reset isn't "who chose the password" — it's whether the plaintext ends
    up somewhere durable and outside the app's control:

    1. A systemd credential named 'admin_password' (LoadCredential=, read
       via $CREDENTIALS_DIRECTORY) — exposed to the process only via a
       private, per-invocation directory, never logged or persisted
       anywhere else. The only source that doesn't force a reset.
    2. ADMIN_PASSWORD, a plaintext env var — has to persist at rest
       somewhere (a Quadlet unit, a .env file) to survive restarts.
    3. The default: a freshly generated random password, printed to
       stdout once. High entropy doesn't help here — the risk isn't
       guessing, it's that container stdout routinely ends up retained
       indefinitely in journald or shipped to log-aggregation
       infrastructure with broader read access than a single config file.
       Both (2) and (3) force a reset for this reason.
    """
    creds_dir = app.config.get('CREDENTIALS_DIRECTORY')
    if creds_dir:
        cred_path = Path(creds_dir) / ADMIN_CREDENTIAL_NAME
        if cred_path.is_file():
            content = cred_path.read_text().strip()
            if content:
                return content, False, 'credential'

    env_password = app.config.get('ADMIN_PASSWORD')
    if env_password:
        return env_password, True, 'env'

    alphabet = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(alphabet) for _ in range(ADMIN_PASSWORD_LENGTH))
    return password, True, 'generated'


def _bootstrap_admin(session, app):
    password, must_reset, source = _initial_admin_password(app)

    session.add(User(
        username=ADMIN_USERNAME,
        role='admin',
        auth_source='local',
        password_hash=generate_password_hash(password, method='scrypt'),
        must_reset_password=must_reset,
    ))

    if source == 'credential':
        print(
            f"Drawbridge: created initial admin user '{ADMIN_USERNAME}' from the "
            f"'{ADMIN_CREDENTIAL_NAME}' systemd credential."
        )
    elif source == 'env':
        print(
            f"Drawbridge: created initial admin user '{ADMIN_USERNAME}' from "
            f"ADMIN_PASSWORD — a password reset will be required on first login."
        )
    else:
        print(
            f"Drawbridge: created initial admin user '{ADMIN_USERNAME}', "
            f"password: {password} — record this now, it will not be shown again. "
            f"A password reset will be required on first login."
        )


def _close_session(exception=None):
    session = g.pop('db_session', None)
    if session is not None:
        if exception:
            session.rollback()
        session.close()
