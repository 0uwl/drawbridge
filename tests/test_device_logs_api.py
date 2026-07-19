import pytest

from drawbridge.db import get_session
from drawbridge.models import Device, ProvisioningSession
from drawbridge.queries import add_device_log_entry

BASE = '/api/v1'


@pytest.fixture()
def device(app):
    with app.app_context():
        session = get_session()
        d = Device(serial='FJC2517X0AB', mac='aa:bb:cc:dd:ee:ff', added_by='operator')
        session.add(d)
        session.commit()
    return d


@pytest.fixture()
def active_session(app, device):
    with app.app_context():
        session = get_session()
        ps = ProvisioningSession(serial=device.serial, ip='192.168.100.15', state='lease_approved')
        session.add(ps)
        session.commit()
    return ps


# GET /api/v1/device-logs

def test_get_device_logs_returns_401_when_not_logged_in(client):
    response = client.get(f'{BASE}/device-logs')
    assert response.status_code == 401


def test_get_device_logs_returns_200_for_operator(app, logged_in_client):
    with app.app_context():
        add_device_log_entry(get_session(), serial='FJC2517X0AB', source='script', message='hello')
        get_session().commit()

    response = logged_in_client.get(f'{BASE}/device-logs')
    assert response.status_code == 200
    serials = {e['serial'] for e in response.get_json()['payload']}
    assert 'FJC2517X0AB' in serials


def test_get_device_logs_filters_by_serial(app, logged_in_client):
    with app.app_context():
        session = get_session()
        add_device_log_entry(session, serial='SN1', source='script', message='a')
        add_device_log_entry(session, serial='SN2', source='script', message='b')
        session.commit()

    response = logged_in_client.get(f'{BASE}/device-logs', query_string={'serial': 'SN1'})
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert len(payload) == 1
    assert payload[0]['serial'] == 'SN1'


# POST /api/v1/device-logs

def test_post_device_log_known_device_persists_row(client, app, device):
    response = client.post(f'{BASE}/device-logs', json={'serial': device.serial, 'message': 'provisioning started'})
    assert response.status_code == 200

    with app.app_context():
        from drawbridge.models import DeviceLogEntry
        rows = get_session().query(DeviceLogEntry).all()
    assert len(rows) == 1
    assert rows[0].serial == device.serial
    assert rows[0].source == 'script'
    assert rows[0].message == 'provisioning started'


def test_post_device_log_active_session_without_device_row_is_accepted(client, app, active_session):
    """A serial with an active ProvisioningSession but no allowlisted Device
    row can still log — mirrors leases.py's own gate shape rather than
    requiring both."""
    response = client.post(f'{BASE}/device-logs', json={'serial': active_session.serial, 'message': 'hi'})
    assert response.status_code == 200


def test_post_device_log_unknown_serial_returns_404(client):
    response = client.post(f'{BASE}/device-logs', json={'serial': 'UNKNOWN-0001', 'message': 'hi'})
    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_found'


def test_post_device_log_missing_message_returns_422(client, device):
    response = client.post(f'{BASE}/device-logs', json={'serial': device.serial})
    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


def test_post_device_log_missing_serial_returns_422(client):
    response = client.post(f'{BASE}/device-logs', json={'message': 'hi'})
    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


def test_post_device_log_accepts_body_without_content_type(client, app, device):
    """force=True: IOS XE's `copy` sends PUT/POST without
    Content-Type: application/json (see leases.py's provision_complete)."""
    response = client.post(
        f'{BASE}/device-logs',
        data=b'{"serial": "%s", "message": "hi"}' % device.serial.encode(),
    )
    assert response.status_code == 200


def test_post_device_log_does_not_require_login(client, device):
    """Open route, no auth decorator — same posture as provision-request/
    provision-complete: gated by serial lookup, not caller identity."""
    response = client.post(f'{BASE}/device-logs', json={'serial': device.serial, 'message': 'hi'})
    assert response.status_code == 200
