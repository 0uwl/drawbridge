"""PostgreSQL counterparts of tests/test_db.py's SQLite bootstrap tests.
The pg_url fixture (conftest.py) starts a throwaway container for the
session and tears it down after — see tests/postgres-integration/README.md.
Skips cleanly if podman isn't available."""
import multiprocessing

import pytest
from sqlalchemy import create_engine, text

from drawbridge import create_app
from drawbridge.db import get_session
from drawbridge.models import Device, ProvisioningLog, ProvisioningSession, Setting, User


@pytest.fixture()
def clean_pg(pg_url):
    """Drops and recreates the public schema before each test, giving the
    same fresh-empty-database guarantee tmp_path gives the SQLite tests —
    lets the whole file reuse one long-lived container."""
    engine = create_engine(pg_url, isolation_level='AUTOCOMMIT')
    with engine.connect() as connection:
        connection.execute(text('DROP SCHEMA public CASCADE'))
        connection.execute(text('CREATE SCHEMA public'))
    engine.dispose()


def test_bootstrap_creates_schema_and_seeds_settings(clean_pg, pg_url, tmp_path):
    app = create_app({'TESTING': True, 'DATABASE_PATH': pg_url, 'FILES_PATH': str(tmp_path / 'files')})

    with app.app_context():
        setting = get_session().get(Setting, 'log_retention_days')
        assert setting is not None
        assert setting.value == app.config['LOG_RETENTION_DAYS']


def test_bootstrap_creates_exactly_one_admin_and_does_not_rerun_on_second_start(clean_pg, pg_url, tmp_path, capsys):
    files_path = str(tmp_path / 'files')

    create_app({'TESTING': True, 'DATABASE_PATH': pg_url, 'FILES_PATH': files_path})
    capsys.readouterr()  # discard the first run's printed password

    app2 = create_app({'TESTING': True, 'DATABASE_PATH': pg_url, 'FILES_PATH': files_path})

    assert 'created initial admin user' not in capsys.readouterr().out
    with app2.app_context():
        assert get_session().query(User).filter_by(username='admin').count() == 1


def _bootstrap_worker(database_path, files_path, barrier, result_queue):
    """multiprocessing.Process target — module-level so it's usable
    regardless of start method. Postgres counterpart of
    test_db.py::_bootstrap_worker; DATABASE_PATH accepts a Postgres URL
    unchanged, so only the fixture supplying it differs."""
    barrier.wait()  # align all processes' entry into init_db() as tightly as possible
    create_app({'TESTING': True, 'DATABASE_PATH': database_path, 'FILES_PATH': files_path})
    result_queue.put(True)


def test_concurrent_first_run_bootstraps_exactly_once_against_postgres(clean_pg, pg_url, tmp_path):
    """Exercises _postgres_lock (pg_advisory_lock/unlock in drawbridge/db.py)
    — the code path docs/testing.md's coverage note flagged as untested.
    Postgres counterpart of test_db.py::test_concurrent_first_run_bootstraps_exactly_once."""
    files_path = str(tmp_path / 'files')
    process_count = 4

    barrier = multiprocessing.Barrier(process_count)
    result_queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(target=_bootstrap_worker, args=(pg_url, files_path, barrier, result_queue))
        for _ in range(process_count)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)

    assert all(p.exitcode == 0 for p in processes)
    assert result_queue.qsize() == process_count

    app = create_app({'TESTING': True, 'DATABASE_PATH': pg_url, 'FILES_PATH': files_path})
    with app.app_context():
        session = get_session()
        assert session.query(User).filter_by(username='admin').count() == 1
        assert session.query(Setting).filter_by(key='log_retention_days').count() == 1


def _provision_request_worker(database_path, files_path, serial, barrier, result_queue):
    """Postgres counterpart of test_lease_api.py::_provision_request_worker
    — proves multiple real workers (the WORKERS>1 scenario
    gunicorn.conf.py only allows under a non-SQLite backend) can write
    ProvisioningSession rows concurrently under Postgres MVCC."""
    app = create_app({'TESTING': True, 'DATABASE_PATH': database_path, 'FILES_PATH': files_path})
    client = app.test_client()
    barrier.wait()
    response = client.get('/api/v1/provision-request', query_string={'serial': serial})
    result_queue.put((serial, response.status_code))


def test_concurrent_provision_requests_against_postgres(clean_pg, pg_url, tmp_path):
    files_path = str(tmp_path / 'files')
    serials = [f'FJC2517X0{i:02d}' for i in range(4)]

    app = create_app({'TESTING': True, 'DATABASE_PATH': pg_url, 'FILES_PATH': files_path})
    with app.app_context():
        session = get_session()
        for serial in serials:
            session.add(Device(serial=serial, mac=f'aa:bb:cc:dd:ee:{serial[-2:]}', added_by='operator'))
        session.commit()

    barrier = multiprocessing.Barrier(len(serials))
    result_queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_provision_request_worker,
            args=(pg_url, files_path, serial, barrier, result_queue),
        )
        for serial in serials
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)

    assert all(p.exitcode == 0 for p in processes)
    assert result_queue.qsize() == len(serials)
    assert all(status == 200 for _, status in (result_queue.get() for _ in serials))

    with app.app_context():
        session = get_session()
        for serial in serials:
            assert session.get(ProvisioningSession, serial) is not None


def test_device_and_provision_round_trip_via_flask_client(clean_pg, pg_url, tmp_path):
    """Basic add-device -> provision-request -> provision-complete round
    trip against Postgres, to catch ORM/dialect issues (autoincrement PKs
    on ProvisioningLog.id, boolean User.is_active from bootstrap) that the
    bootstrap-only tests above wouldn't."""
    app = create_app({'TESTING': True, 'DATABASE_PATH': pg_url, 'FILES_PATH': str(tmp_path / 'files')})
    client = app.test_client()

    with app.app_context():
        session = get_session()
        session.add(Device(serial='FJC2517X0AB', mac='aa:bb:cc:dd:ee:ff', added_by='operator'))
        session.commit()

    request_response = client.get('/api/v1/provision-request', query_string={'serial': 'FJC2517X0AB'})
    assert request_response.status_code == 200

    complete_response = client.put(
        '/api/v1/provision-complete',
        json={'serial': 'FJC2517X0AB', 'image': 'ios-xe-17.9.bin'},
    )
    assert complete_response.status_code == 200

    with app.app_context():
        session = get_session()
        assert session.get(ProvisioningSession, 'FJC2517X0AB') is None
        log_rows = session.query(ProvisioningLog).filter_by(serial='FJC2517X0AB').all()
        assert len(log_rows) == 1
        assert log_rows[0].image == 'ios-xe-17.9.bin'
        assert log_rows[0].id is not None
