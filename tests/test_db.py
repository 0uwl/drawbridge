import multiprocessing
import sqlite3

from sqlalchemy import text

from drawbridge import create_app
from drawbridge.db import _database_url, _is_sqlite, get_session
from drawbridge.models import Setting, User


def test_init_db_seeds_log_retention_from_config(app):
    with app.app_context():
        session = get_session()
        setting = session.get(Setting, 'log_retention_days')

        assert setting is not None
        assert setting.value == app.config['LOG_RETENTION_DAYS']


def test_init_db_seeds_default_image_when_configured(tmp_path):
    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'DEFAULT_IMAGE': 'ios-xe-17.9.bin',
    })
    with app.app_context():
        setting = get_session().get(Setting, 'default_image')
        assert setting is not None
        assert setting.value == 'ios-xe-17.9.bin'


def test_init_db_does_not_seed_default_image_when_not_configured(app):
    with app.app_context():
        assert get_session().get(Setting, 'default_image') is None


def test_init_db_seeds_default_config_file_when_configured(tmp_path):
    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'DEFAULT_CONFIG_FILE': 'spine.cfg',
    })
    with app.app_context():
        setting = get_session().get(Setting, 'default_config_file')
        assert setting is not None
        assert setting.value == 'spine.cfg'


def test_init_db_does_not_seed_default_config_file_when_not_configured(app):
    with app.app_context():
        assert get_session().get(Setting, 'default_config_file') is None


def test_init_db_seeds_default_script_when_configured(tmp_path):
    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'DEFAULT_SCRIPT': 'ztp-base.py',
    })
    with app.app_context():
        setting = get_session().get(Setting, 'default_script')
        assert setting is not None
        assert setting.value == 'ztp-base.py'


def test_init_db_does_not_seed_default_script_when_not_configured(app):
    with app.app_context():
        assert get_session().get(Setting, 'default_script') is None


def test_init_db_bootstraps_exactly_one_admin_user(app):
    with app.app_context():
        session = get_session()
        admins = session.query(User).filter_by(username='admin').all()

        assert len(admins) == 1
        assert admins[0].role == 'admin'
        assert admins[0].auth_source == 'local'
        assert admins[0].password_hash


def test_admin_bootstrap_password_is_printed_once(tmp_path, capsys):
    # built directly (not via the `app` fixture) so the bootstrap print
    # happens during this test's capsys-captured call phase, not fixture setup
    create_app({'TESTING': True, 'DATABASE_PATH': str(tmp_path / 'drawbridge.db'), 'FILES_PATH': str(tmp_path / 'files')})

    assert "created initial admin user 'admin'" in capsys.readouterr().out


def test_init_db_does_not_rebootstrap_an_existing_database(tmp_path, capsys):
    db_path = str(tmp_path / 'drawbridge.db')
    files_path = str(tmp_path / 'files')

    create_app({'TESTING': True, 'DATABASE_PATH': db_path, 'FILES_PATH': files_path})
    capsys.readouterr()  # discard the first run's printed password

    app2 = create_app({'TESTING': True, 'DATABASE_PATH': db_path, 'FILES_PATH': files_path})

    assert "created initial admin user" not in capsys.readouterr().out
    with app2.app_context():
        session = get_session()
        assert session.query(User).filter_by(username='admin').count() == 1


# Bootstrap admin password sources — see docs/authentication.md

def test_admin_bootstrap_defaults_to_random_password_and_forces_reset(app):
    # High entropy protects against guessing, not against the stdout print
    # ending up retained in journald/log-aggregation — see docs/decisions.md.
    with app.app_context():
        admin = get_session().query(User).filter_by(username='admin').first()
        assert admin.must_reset_password is True


def test_admin_bootstrap_from_admin_password_env_forces_reset(tmp_path):
    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'ADMIN_PASSWORD': 'env-seeded-password',
    })
    with app.app_context():
        admin = get_session().query(User).filter_by(username='admin').first()
        assert admin.must_reset_password is True
        assert admin.password_hash is not None


def test_admin_bootstrap_from_admin_password_env_does_not_print_password(tmp_path, capsys):
    create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'ADMIN_PASSWORD': 'env-seeded-password',
    })
    out = capsys.readouterr().out
    assert 'env-seeded-password' not in out
    assert 'password reset will be required' in out


def test_admin_bootstrap_from_systemd_credential_does_not_force_reset(tmp_path):
    creds_dir = tmp_path / 'creds'
    creds_dir.mkdir()
    (creds_dir / 'admin_password').write_text('credential-seeded-password\n')

    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'CREDENTIALS_DIRECTORY': str(creds_dir),
    })
    with app.app_context():
        admin = get_session().query(User).filter_by(username='admin').first()
        assert admin.must_reset_password is False

    client = app.test_client()
    response = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'credential-seeded-password'})
    assert response.status_code == 200
    assert response.get_json()['payload']['username'] == 'admin'


def test_admin_bootstrap_prefers_systemd_credential_over_admin_password_env(tmp_path):
    creds_dir = tmp_path / 'creds'
    creds_dir.mkdir()
    (creds_dir / 'admin_password').write_text('credential-wins')

    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'CREDENTIALS_DIRECTORY': str(creds_dir),
        'ADMIN_PASSWORD': 'env-should-be-ignored',
    })
    with app.app_context():
        admin = get_session().query(User).filter_by(username='admin').first()
        assert admin.must_reset_password is False

    client = app.test_client()
    response = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'credential-wins'})
    assert response.status_code == 200


def test_admin_bootstrap_ignores_empty_systemd_credential_file(tmp_path):
    creds_dir = tmp_path / 'creds'
    creds_dir.mkdir()
    (creds_dir / 'admin_password').write_text('')

    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'CREDENTIALS_DIRECTORY': str(creds_dir),
        'ADMIN_PASSWORD': 'env-fallback-password',
    })
    with app.app_context():
        admin = get_session().query(User).filter_by(username='admin').first()
        # Empty credential file falls through to the next priority (ADMIN_PASSWORD)
        assert admin.must_reset_password is True

    client = app.test_client()
    response = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'env-fallback-password'})
    assert response.status_code == 200
    assert response.get_json()['payload']['must_reset_password'] is True


def test_sqlite_pragmas_are_set_on_connect(app):
    with app.app_context():
        session = get_session()
        journal_mode = session.execute(text('PRAGMA journal_mode')).scalar()
        busy_timeout = session.execute(text('PRAGMA busy_timeout')).scalar()

        assert journal_mode == 'wal'
        assert busy_timeout == app.config['SQLITE_BUSY_TIMEOUT_MS']


def test_database_url_wraps_a_bare_filesystem_path():
    assert _database_url('/tmp/drawbridge.db') == 'sqlite:////tmp/drawbridge.db'


def test_database_url_passes_through_an_existing_url():
    url = 'postgresql://user:pass@host/dbname'
    assert _database_url(url) == url


def test_is_sqlite_true_for_a_bare_filesystem_path():
    assert _is_sqlite('/tmp/drawbridge.db') is True


def test_is_sqlite_false_for_a_postgres_url():
    assert _is_sqlite('postgresql+psycopg://user:pass@host/dbname') is False


def _bootstrap_worker(database_path, files_path, barrier, result_queue):
    """multiprocessing.Process target — module-level so it's usable
    regardless of start method. Calls create_app() directly (bypassing
    gunicorn.conf.py's worker-count enforcement entirely) so this test
    verifies the bootstrap lock itself, not the belt-and-suspenders
    single-worker constraint layered on top of it.
    """
    barrier.wait()  # align all processes' entry into init_db() as tightly as possible
    create_app({
        'TESTING': True,
        'DATABASE_PATH': database_path,
        'FILES_PATH': files_path,
    })
    result_queue.put(True)


def test_concurrent_first_run_bootstraps_exactly_once(tmp_path):
    """Reproduces the race in docs/kea-hook-findings.md #5: several real OS
    processes (not threads — the race is between processes, per Gunicorn's
    post-fork model) racing init_db() against one fresh DATABASE_PATH.
    Asserts the fcntl-based bootstrap lock in drawbridge/db.py serializes
    them so exactly one admin user and one of each seeded Setting survive,
    and no worker crashes.
    """
    database_path = str(tmp_path / 'drawbridge.db')
    files_path = str(tmp_path / 'files')
    process_count = 4

    barrier = multiprocessing.Barrier(process_count)
    result_queue = multiprocessing.Queue()

    processes = [
        multiprocessing.Process(
            target=_bootstrap_worker,
            args=(database_path, files_path, barrier, result_queue),
        )
        for _ in range(process_count)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)

    assert all(p.exitcode == 0 for p in processes)
    assert result_queue.qsize() == process_count

    app = create_app({'TESTING': True, 'DATABASE_PATH': database_path, 'FILES_PATH': files_path})
    with app.app_context():
        session = get_session()
        assert session.query(User).filter_by(username='admin').count() == 1
        assert session.query(Setting).filter_by(key='log_retention_days').count() == 1


# Schema evolution — see docs/database.md, "Schema evolution"

def _indexed_columns(session, table_name):
    index_rows = session.execute(text(f"PRAGMA index_list('{table_name}')")).all()
    columns = set()
    for row in index_rows:
        index_name = row[1]
        columns.update(r[2] for r in session.execute(text(f"PRAGMA index_info('{index_name}')")).all())
    return columns


def test_sync_indexes_adds_a_missing_index_to_an_already_existing_table(tmp_path):
    """Regression for create_all()'s upgrade gap: it only emits CREATE INDEX
    while creating a brand-new table, so an index= added to a model in a
    later version would otherwise never reach an already-deployed database.
    Simulates that by hand-creating device_logs the way a pre-index install
    would have it, then asserting init_db() (via create_app()) adds the
    missing index rather than silently skipping the existing table."""
    database_path = str(tmp_path / 'drawbridge.db')

    conn = sqlite3.connect(database_path)
    conn.execute("""
        CREATE TABLE device_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serial TEXT,
            source TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    app = create_app({'TESTING': True, 'DATABASE_PATH': database_path, 'FILES_PATH': str(tmp_path / 'files')})

    with app.app_context():
        assert 'timestamp' in _indexed_columns(get_session(), 'device_logs')


def test_sync_indexes_is_idempotent_across_repeated_startups(tmp_path):
    database_path = str(tmp_path / 'drawbridge.db')
    files_path = str(tmp_path / 'files')

    create_app({'TESTING': True, 'DATABASE_PATH': database_path, 'FILES_PATH': files_path})
    app = create_app({'TESTING': True, 'DATABASE_PATH': database_path, 'FILES_PATH': files_path})

    with app.app_context():
        assert 'timestamp' in _indexed_columns(get_session(), 'device_logs')
        assert 'timestamp' in _indexed_columns(get_session(), 'provisioning_log')
