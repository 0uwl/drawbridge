import multiprocessing

import pytest

from drawbridge import create_app
from drawbridge.db import get_session
from drawbridge.models import Device, ProvisioningLog, ProvisioningSession

BASE = '/api/v1'


@pytest.fixture()
def device(app):
    with app.app_context():
        session = get_session()
        d = Device(
            serial='FJC2517X0AB', mac='aa:bb:cc:dd:ee:ff', added_by='operator',
            image='cat9k_iosxe.SPA.bin', config_file='base.cfg',
        )
        session.add(d)
        session.commit()
    return d


@pytest.fixture()
def active_session(app, device):
    with app.app_context():
        session = get_session()
        ps = ProvisioningSession(
            serial=device.serial, mac=device.mac, ip='192.168.100.15', state='lease_approved',
            image=device.image, config_file=device.config_file,
        )
        session.add(ps)
        session.commit()
    return ps


# GET /api/v1/provision-request

def test_provision_request_known_serial_returns_200_and_creates_session(client, app, device):
    response = client.get(f'{BASE}/provision-request', query_string={'serial': device.serial})

    assert response.status_code == 200
    assert response.get_json()['success'] is True

    with app.app_context():
        ps = get_session().get(ProvisioningSession, device.serial)
        assert ps is not None
        # ip is captured from the caller, image/config_file from the device's
        # assignment — both are needed for the Active Sessions UI to show
        # anything other than "—" (see docs/frontend.md).
        assert ps.ip is not None
        assert ps.image == device.image
        assert ps.config_file == device.config_file


def test_provision_request_unknown_serial_returns_404(client, app):
    response = client.get(f'{BASE}/provision-request', query_string={'serial': 'UNKNOWN-0001'})

    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_found'

    with app.app_context():
        assert get_session().get(ProvisioningSession, 'UNKNOWN-0001') is None


def test_provision_request_missing_serial_returns_422(client):
    response = client.get(f'{BASE}/provision-request')

    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


def test_provision_request_is_idempotent_on_repeat_calls(client, app, device):
    client.get(f'{BASE}/provision-request', query_string={'serial': device.serial, 'mac': 'aa:aa:aa:aa:aa:aa'})
    response = client.get(f'{BASE}/provision-request', query_string={'serial': device.serial, 'mac': 'bb:bb:bb:bb:bb:bb'})

    assert response.status_code == 200

    with app.app_context():
        session = get_session().get(ProvisioningSession, device.serial)
        assert session.mac == 'bb:bb:bb:bb:bb:bb'


# PUT/POST /api/v1/provision-complete

def test_provision_complete_known_active_session_returns_200(client, app, active_session):
    response = client.put(f'{BASE}/provision-complete', json={'serial': active_session.serial, 'image': 'ios-xe-17.9.bin'})

    assert response.status_code == 200

    with app.app_context():
        session = get_session()
        assert session.get(ProvisioningSession, active_session.serial) is None


def test_provision_complete_falls_back_to_session_image_and_config_when_not_reported(client, app, active_session):
    # scripts/ztp-base.py's alpha stub never reports image/config_file in
    # its completion payload — without the session fallback, the log (and
    # therefore the Devices UI's "Provisioning" badge) would show blank
    # image/config for every real-world completion despite the assignment
    # being known since provision-request.
    response = client.put(f'{BASE}/provision-complete', json={'serial': active_session.serial})

    assert response.status_code == 200

    with app.app_context():
        entry = get_session().query(ProvisioningLog).filter_by(serial=active_session.serial).one()
        assert entry.image == active_session.image
        assert entry.config_file == active_session.config_file


def test_provision_complete_explicit_image_and_config_override_session_fallback(client, app, active_session):
    response = client.put(
        f'{BASE}/provision-complete',
        json={'serial': active_session.serial, 'image': 'reported.bin', 'config_file': 'reported.cfg'},
    )

    assert response.status_code == 200

    with app.app_context():
        entry = get_session().query(ProvisioningLog).filter_by(serial=active_session.serial).one()
        assert entry.image == 'reported.bin'
        assert entry.config_file == 'reported.cfg'


def test_provision_complete_unknown_serial_returns_404(client):
    response = client.put(f'{BASE}/provision-complete', json={'serial': 'UNKNOWN-0001'})

    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_active'


def test_provision_complete_missing_serial_returns_422(client):
    response = client.put(f'{BASE}/provision-complete', json={})

    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


# Steady-state concurrency

def _provision_request_worker(database_path, files_path, serial, barrier, result_queue):
    """multiprocessing.Process target — module-level so it's usable
    regardless of start method. Simulates multiple devices phoning home at
    once (the normal-operation case), as opposed to
    test_db.py's one-time bootstrap race.
    """
    # create_app() first, then barrier — the write itself must be
    # synchronized, not process startup, or jitter spreads the requests out
    # enough that they never actually contend for the same DB file.
    app = create_app({'TESTING': True, 'DATABASE_PATH': database_path, 'FILES_PATH': files_path})
    client = app.test_client()
    barrier.wait()
    response = client.get(f'{BASE}/provision-request', query_string={'serial': serial})
    result_queue.put((serial, response.status_code))


def test_concurrent_provision_requests_from_different_devices_all_succeed(tmp_path):
    database_path = str(tmp_path / 'drawbridge.db')
    files_path = str(tmp_path / 'files')
    serials = [f'FJC2517X0{i:02d}' for i in range(4)]

    app = create_app({'TESTING': True, 'DATABASE_PATH': database_path, 'FILES_PATH': files_path})
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
            args=(database_path, files_path, serial, barrier, result_queue),
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
