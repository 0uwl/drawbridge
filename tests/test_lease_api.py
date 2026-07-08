import pytest

from drawbridge.db import get_session
from drawbridge.models import Device, ProvisioningSession

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
        ps = ProvisioningSession(serial=device.serial, mac=device.mac, ip='192.168.100.15', state='lease_approved')
        session.add(ps)
        session.commit()
    return ps


# GET /api/v1/provision-request

def test_provision_request_known_serial_returns_200_and_creates_session(client, app, device):
    response = client.get(f'{BASE}/provision-request', query_string={'serial': device.serial})

    assert response.status_code == 200
    assert response.get_json()['success'] is True

    with app.app_context():
        assert get_session().get(ProvisioningSession, device.serial) is not None


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


def test_provision_complete_unknown_serial_returns_404(client):
    response = client.put(f'{BASE}/provision-complete', json={'serial': 'UNKNOWN-0001'})

    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_active'


def test_provision_complete_missing_serial_returns_422(client):
    response = client.put(f'{BASE}/provision-complete', json={})

    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'
