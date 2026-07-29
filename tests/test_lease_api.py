import multiprocessing

import pytest

from drawbridge import create_app
from drawbridge.db import get_session
from drawbridge.models import Device, DeviceLogEntry, ProvisioningLog, ProvisioningSession, ZTPFile

BASE = '/api/v1'

# Matches the `device` fixture's Device.version below, so most tests exercise
# the "device is already at its desired version" path (image omitted from
# the response) without needing a ZTPFile fixture too. Tests that actually
# exercise the upgrade decision set their own versions explicitly.
CURRENT_VERSION = '17.9.1'


def _request(client, serial, version=CURRENT_VERSION, mac=None, **kwargs):
    query_string = {'serial': serial, 'version': version}
    if mac is not None:
        query_string['mac'] = mac
    return client.get(f'{BASE}/provision-request', query_string=query_string, **kwargs)


@pytest.fixture()
def device(app):
    with app.app_context():
        session = get_session()
        d = Device(
            serial='FJC2517X0AB', mac='aa:bb:cc:dd:ee:ff', added_by='operator',
            version=CURRENT_VERSION, config_file='base.cfg',
        )
        session.add(d)
        session.commit()
    return d


@pytest.fixture()
def active_session(app, device):
    with app.app_context():
        session = get_session()
        ps = ProvisioningSession(
            serial=device.serial, mac=device.mac, ip='127.0.0.1', state='lease_approved',
            image=None, config_file=device.config_file,
        )
        session.add(ps)
        session.commit()
    return ps


# GET /api/v1/provision-request

def test_provision_request_known_serial_returns_200_and_creates_session(client, app, device):
    response = _request(client, device.serial)

    assert response.status_code == 200
    assert response.get_json()['success'] is True

    with app.app_context():
        ps = get_session().get(ProvisioningSession, device.serial)
        assert ps is not None
        # ip is captured from the caller; config_file from the device's
        # assignment — both are needed for the Active Sessions UI to show
        # anything other than "—" (see docs/frontend.md).
        assert ps.ip is not None
        assert ps.image is None  # device already reports its desired version
        assert ps.config_file == device.config_file


def test_provision_request_unknown_serial_returns_404(client, app):
    response = _request(client, 'UNKNOWN-0001')

    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_found'

    with app.app_context():
        assert get_session().get(ProvisioningSession, 'UNKNOWN-0001') is None


def test_provision_request_missing_serial_returns_422(client):
    response = client.get(f'{BASE}/provision-request', query_string={'version': CURRENT_VERSION})

    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


def test_provision_request_missing_version_returns_422(client, device):
    response = client.get(f'{BASE}/provision-request', query_string={'serial': device.serial})

    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


def test_provision_request_rejects_mismatched_mac_on_repeat_call(client, app, device):
    _request(client, device.serial, mac='aa:aa:aa:aa:aa:aa')
    response = _request(client, device.serial, mac='bb:bb:bb:bb:bb:bb')

    assert response.status_code == 409
    assert response.get_json()['error'] == 'session_mismatch'

    with app.app_context():
        session = get_session().get(ProvisioningSession, device.serial)
        assert session.mac == 'aa:aa:aa:aa:aa:aa'


def test_provision_request_matching_repeat_call_still_succeeds(client, app, device):
    _request(client, device.serial, mac='aa:aa:aa:aa:aa:aa')
    response = _request(client, device.serial, mac='aa:aa:aa:aa:aa:aa')

    assert response.status_code == 200

    with app.app_context():
        session = get_session().get(ProvisioningSession, device.serial)
        assert session.mac == 'aa:aa:aa:aa:aa:aa'


def test_provision_request_rejects_mismatched_ip_on_repeat_call(client, app, device):
    _request(client, device.serial, environ_overrides={'REMOTE_ADDR': '10.0.0.5'})
    response = _request(client, device.serial, environ_overrides={'REMOTE_ADDR': '10.0.0.9'})

    assert response.status_code == 409
    assert response.get_json()['error'] == 'session_mismatch'

    with app.app_context():
        session = get_session().get(ProvisioningSession, device.serial)
        assert session.ip == '10.0.0.5'


def test_provision_request_allows_filling_in_mac_not_previously_provided(client, app, device):
    _request(client, device.serial)
    response = _request(client, device.serial, mac='aa:aa:aa:aa:aa:aa')

    assert response.status_code == 200

    with app.app_context():
        session = get_session().get(ProvisioningSession, device.serial)
        assert session.mac == 'aa:aa:aa:aa:aa:aa'


def test_provision_request_omitting_mac_on_repeat_call_is_not_a_mismatch(client, app, device):
    _request(client, device.serial, mac='aa:aa:aa:aa:aa:aa')
    response = _request(client, device.serial)

    assert response.status_code == 200

    with app.app_context():
        session = get_session().get(ProvisioningSession, device.serial)
        assert session.mac == 'aa:aa:aa:aa:aa:aa'


# Upgrade decision — see docs/decisions.md, "Version-based image mapping"

def test_provision_request_omits_image_when_already_at_desired_version(client, app, device):
    response = _request(client, device.serial, version=CURRENT_VERSION)

    assert response.status_code == 200
    assert response.get_json()['payload']['image'] is None


def test_provision_request_includes_image_when_version_differs(client, app, device):
    with app.app_context():
        session = get_session()
        session.add(ZTPFile(file_type='image', filename='ios-xe-17.9.1.bin', size_bytes=1, sha256='a' * 64, version=CURRENT_VERSION))
        session.commit()

    response = _request(client, device.serial, version='17.0.0')

    assert response.status_code == 200
    assert response.get_json()['payload']['image'] == 'ios-xe-17.9.1.bin'

    with app.app_context():
        ps = get_session().get(ProvisioningSession, device.serial)
        assert ps.image == 'ios-xe-17.9.1.bin'


def test_provision_request_denies_when_desired_version_has_no_mapped_image(client, app, device):
    # device.version (CURRENT_VERSION) has no ZTPFile mapped to it, and the
    # reported version differs — fail closed rather than proceed with
    # config only (docs/decisions.md, "fail closed everywhere").
    response = _request(client, device.serial, version='17.0.0')

    assert response.status_code == 409
    assert response.get_json()['error'] == 'image_missing_for_version'

    with app.app_context():
        assert get_session().get(ProvisioningSession, device.serial) is None


def test_provision_request_no_version_concept_when_device_version_unset(client, app):
    with app.app_context():
        session = get_session()
        session.add(Device(serial='NO-VERSION', added_by='operator'))
        session.commit()

    response = _request(client, 'NO-VERSION', version='17.0.0')

    assert response.status_code == 200
    assert response.get_json()['payload']['image'] is None


# Wildcard allowlist entry — see docs/decisions.md, "Wildcard allowlist entry"

def test_provision_request_falls_back_to_wildcard_entry(client, app):
    with app.app_context():
        session = get_session()
        session.add(Device(serial='*', added_by='operator', config_file='shared.cfg'))
        session.commit()

    response = _request(client, 'UNREGISTERED-0001', version='17.0.0')

    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['serial'] == 'UNREGISTERED-0001'
    assert payload['config_file'] == 'shared.cfg'

    with app.app_context():
        ps = get_session().get(ProvisioningSession, 'UNREGISTERED-0001')
        assert ps is not None
        assert ps.config_file == 'shared.cfg'


def test_provision_request_prefers_exact_match_over_wildcard(client, app, device):
    with app.app_context():
        session = get_session()
        session.add(Device(serial='*', added_by='operator', config_file='shared.cfg'))
        session.commit()

    response = _request(client, device.serial)

    assert response.status_code == 200
    assert response.get_json()['payload']['config_file'] == device.config_file


# PUT/POST /api/v1/provision-request/facts

def test_provision_request_facts_known_active_session_returns_200_and_records_facts(client, app, active_session):
    response = client.put(
        f'{BASE}/provision-request/facts',
        json={'serial': active_session.serial, 'model': 'C9200CX-12P-2X2G', 'version': '17.9.1'},
    )

    assert response.status_code == 200
    assert response.get_json()['success'] is True

    with app.app_context():
        ps = get_session().get(ProvisioningSession, active_session.serial)
        assert ps.model == 'C9200CX-12P-2X2G'
        assert ps.version == '17.9.1'


def test_provision_request_facts_unknown_serial_returns_404(client):
    response = client.put(f'{BASE}/provision-request/facts', json={'serial': 'UNKNOWN-0001', 'model': 'm'})

    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_active'


def test_provision_request_facts_missing_serial_returns_422(client):
    response = client.put(f'{BASE}/provision-request/facts', json={'model': 'm'})

    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


def test_provision_request_facts_empty_body_returns_422(client, active_session):
    response = client.put(
        f'{BASE}/provision-request/facts', data='not json', content_type='application/json',
    )

    assert response.status_code == 422
    assert response.get_json()['error'] == 'empty_request_body'


def test_provision_request_facts_rejects_mismatched_ip(client, app, active_session):
    response = client.put(
        f'{BASE}/provision-request/facts',
        json={'serial': active_session.serial, 'model': 'm'},
        environ_overrides={'REMOTE_ADDR': '10.0.0.99'},
    )

    assert response.status_code == 409
    assert response.get_json()['error'] == 'session_mismatch'

    with app.app_context():
        ps = get_session().get(ProvisioningSession, active_session.serial)
        assert ps.model is None


def test_provision_request_facts_accepts_post_too(client, active_session):
    response = client.post(
        f'{BASE}/provision-request/facts', json={'serial': active_session.serial, 'model': 'm', 'version': 'v'},
    )

    assert response.status_code == 200


# PUT/POST /api/v1/provision-complete

def test_provision_complete_known_active_session_returns_200(client, app, active_session):
    response = client.put(f'{BASE}/provision-complete', json={'serial': active_session.serial, 'image': 'ios-xe-17.9.bin'})

    assert response.status_code == 200

    with app.app_context():
        session = get_session()
        assert session.get(ProvisioningSession, active_session.serial) is None


def test_provision_complete_falls_back_to_session_image_and_config_when_not_reported(client, app, active_session):
    # scripts/ztp_script.py's alpha stub never reports image/config_file in
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


def test_provision_complete_rejects_mismatched_ip(client, app, active_session):
    response = client.put(
        f'{BASE}/provision-complete', json={'serial': active_session.serial},
        environ_overrides={'REMOTE_ADDR': '10.0.0.99'},
    )

    assert response.status_code == 409
    assert response.get_json()['error'] == 'session_mismatch'

    with app.app_context():
        assert get_session().get(ProvisioningSession, active_session.serial) is not None


def test_provision_complete_unknown_serial_returns_404(client):
    response = client.put(f'{BASE}/provision-complete', json={'serial': 'UNKNOWN-0001'})

    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_active'


def test_provision_complete_missing_serial_returns_422(client):
    response = client.put(f'{BASE}/provision-complete', json={})

    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


# DeviceLogEntry retention on completion — see docs/database.md, "Log
# Retention & Data Minimisation"

def test_provision_complete_success_keeps_device_logs(client, app, active_session):
    # Regression: an operator reviewing the Allowlist page
    # (frontend/src/views/Devices.vue) can click into an already-provisioned
    # device's row and see the raw run that just completed — an earlier
    # version of this policy cleared DeviceLogEntry rows immediately on a
    # clean success, leaving nothing to show there.
    with app.app_context():
        session = get_session()
        session.add(DeviceLogEntry(serial=active_session.serial, source='script', message='provisioning started'))
        session.commit()

    response = client.put(f'{BASE}/provision-complete', json={'serial': active_session.serial})
    assert response.status_code == 200

    with app.app_context():
        remaining = get_session().query(DeviceLogEntry).filter_by(serial=active_session.serial).all()
    assert len(remaining) == 1


def test_provision_complete_failure_keeps_device_logs(client, app, active_session):
    with app.app_context():
        session = get_session()
        session.add(DeviceLogEntry(serial=active_session.serial, source='script', message='provisioning started'))
        session.commit()

    response = client.put(
        f'{BASE}/provision-complete',
        json={'serial': active_session.serial, 'event': 'provision_failed'},
    )
    assert response.status_code == 200

    with app.app_context():
        remaining = get_session().query(DeviceLogEntry).filter_by(serial=active_session.serial).all()
    assert len(remaining) == 1


# ProxyFix / X-Forwarded-For correctness (v0-3-2.md) — once behind
# drawbridge-nginx, every request's direct socket peer is nginx itself, not
# the real device. Without ProxyFix trusting X-Forwarded-For
# (drawbridge/main.py), every device would collapse onto the same apparent
# request.remote_addr, breaking the IP-pinned session model these routes
# depend on (create_provisioning_session's ip pinning above,
# provision_complete's session_mismatch check, and
# queries.find_active_session_by_ip's file-download gate).

def test_provision_request_pins_session_to_x_forwarded_for_ip(client, app, device):
    response = _request(client, device.serial, headers={'X-Forwarded-For': '192.168.100.42'})
    assert response.status_code == 200

    with app.app_context():
        session = get_session().get(ProvisioningSession, device.serial)
        assert session.ip == '192.168.100.42'


def test_provision_request_distinguishes_devices_behind_the_same_proxy_hop(client, app):
    """Two different devices, same direct connecting peer (the test
    client's default REMOTE_ADDR simulates nginx) but different
    X-Forwarded-For — each must still be pinned to its own real IP, not
    both collapsing onto nginx's address."""
    with app.app_context():
        session = get_session()
        session.add(Device(serial='DEV-A', added_by='operator'))
        session.add(Device(serial='DEV-B', added_by='operator'))
        session.commit()

    _request(client, 'DEV-A', headers={'X-Forwarded-For': '192.168.100.10'})
    _request(client, 'DEV-B', headers={'X-Forwarded-For': '192.168.100.20'})

    with app.app_context():
        session = get_session()
        assert session.get(ProvisioningSession, 'DEV-A').ip == '192.168.100.10'
        assert session.get(ProvisioningSession, 'DEV-B').ip == '192.168.100.20'


def test_provision_complete_matches_when_x_forwarded_for_ip_matches_pinned_session(client, app, device):
    _request(client, device.serial, headers={'X-Forwarded-For': '192.168.100.42'})
    response = client.put(
        f'{BASE}/provision-complete', json={'serial': device.serial},
        headers={'X-Forwarded-For': '192.168.100.42'},
    )
    assert response.status_code == 200


def test_provision_complete_rejects_when_x_forwarded_for_ip_differs_from_pinned_session(client, app, device):
    _request(client, device.serial, headers={'X-Forwarded-For': '192.168.100.42'})
    response = client.put(
        f'{BASE}/provision-complete', json={'serial': device.serial},
        headers={'X-Forwarded-For': '192.168.100.99'},
    )
    assert response.status_code == 409
    assert response.get_json()['error'] == 'session_mismatch'


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
    response = client.get(f'{BASE}/provision-request', query_string={'serial': serial, 'version': CURRENT_VERSION})
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
