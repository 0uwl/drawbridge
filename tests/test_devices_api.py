from datetime import datetime, timedelta, timezone

import pytest

from drawbridge.db import get_session
from drawbridge.models import Device, DeviceLogEntry, ProvisioningLog, ProvisioningSession, SESSION_STALE_AFTER_MINUTES, Setting

BASE = '/api/v1'


@pytest.fixture()
def device(app):
    with app.app_context():
        session = get_session()
        d = Device(
            serial='FJC2517X0AB',
            mac='aa:bb:cc:dd:ee:ff',
            description='Test device',
            added_by='operator',
        )
        session.add(d)
        session.commit()
    return d


@pytest.fixture()
def active_session(app, device):
    with app.app_context():
        session = get_session()
        ps = ProvisioningSession(
            serial=device.serial,
            mac=device.mac,
            ip='10.0.0.5',
            state='lease_approved',
        )
        session.add(ps)
        session.commit()
    return ps


@pytest.fixture()
def stale_session(app, device):
    with app.app_context():
        session = get_session()
        last_seen = (datetime.now(timezone.utc) - timedelta(minutes=SESSION_STALE_AFTER_MINUTES + 1)).isoformat(timespec='microseconds')
        ps = ProvisioningSession(
            serial=device.serial,
            mac=device.mac,
            ip='10.0.0.5',
            state='downloading',
            last_seen_at=last_seen,
        )
        session.add(ps)
        session.commit()
    return ps


# GET /api/v1/devices

def test_list_devices_returns_401_when_not_logged_in(client):
    response = client.get(f'{BASE}/devices/')
    assert response.status_code == 401


def test_list_devices_returns_empty_list_when_no_devices(logged_in_client):
    response = logged_in_client.get(f'{BASE}/devices/')
    assert response.status_code == 200
    assert response.get_json()['payload'] == []


def test_list_devices_returns_registered_devices(logged_in_client, device):
    response = logged_in_client.get(f'{BASE}/devices/')
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert len(payload) == 1
    assert payload[0]['serial'] == device.serial


# POST /api/v1/devices

def test_add_device_returns_401_when_not_logged_in(client):
    response = client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB'})
    assert response.status_code == 401


def test_add_device_returns_422_when_serial_missing(logged_in_client):
    response = logged_in_client.post(f'{BASE}/devices/', json={'mac': 'aa:bb:cc:dd:ee:ff'})
    assert response.status_code == 422


def test_add_device_returns_422_on_empty_body(logged_in_client):
    response = logged_in_client.post(f'{BASE}/devices/', json={})
    assert response.status_code == 422


def test_add_device_returns_200_with_serial_only(logged_in_client):
    response = logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB'})
    assert response.status_code == 200


def test_add_device_persists_to_db(app, logged_in_client):
    logged_in_client.post(f'{BASE}/devices/', json={
        'serial': 'FJC2517X0AB',
        'mac': 'aa:bb:cc:dd:ee:ff',
        'description': 'Edge router',
        'image': 'ios-xe-17.9.bin',
        'config_file': 'spine.cfg',
    })
    with app.app_context():
        d = get_session().get(Device, 'FJC2517X0AB')
        assert d is not None
        assert d.mac == 'aa:bb:cc:dd:ee:ff'
        assert d.description == 'Edge router'
        assert d.image == 'ios-xe-17.9.bin'
        assert d.config_file == 'spine.cfg'


def test_add_device_sets_added_by_to_current_user(app, logged_in_client, user):
    logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB'})
    with app.app_context():
        d = get_session().get(Device, 'FJC2517X0AB')
        assert d.added_by == user.username


def test_add_device_uses_default_image_when_not_provided(app, logged_in_client):
    with app.app_context():
        session = get_session()
        session.add(Setting(key='default_image', value='ios-xe-default.bin'))
        session.commit()

    logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB'})

    with app.app_context():
        d = get_session().get(Device, 'FJC2517X0AB')
        assert d.image == 'ios-xe-default.bin'


def test_add_device_uses_default_config_file_when_not_provided(app, logged_in_client):
    with app.app_context():
        session = get_session()
        session.add(Setting(key='default_config_file', value='default.cfg'))
        session.commit()

    logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB'})

    with app.app_context():
        d = get_session().get(Device, 'FJC2517X0AB')
        assert d.config_file == 'default.cfg'


def test_add_device_explicit_image_overrides_default(app, logged_in_client):
    with app.app_context():
        session = get_session()
        session.add(Setting(key='default_image', value='ios-xe-default.bin'))
        session.commit()

    logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB', 'image': 'ios-xe-custom.bin'})

    with app.app_context():
        d = get_session().get(Device, 'FJC2517X0AB')
        assert d.image == 'ios-xe-custom.bin'


def test_add_device_is_idempotent_on_serial(app, logged_in_client, device):
    logged_in_client.post(f'{BASE}/devices/', json={
        'serial': device.serial,
        'mac': '11:22:33:44:55:66',
        'description': 'Updated description',
    })
    with app.app_context():
        d = get_session().get(Device, device.serial)
        assert d.mac == '11:22:33:44:55:66'
        assert d.description == 'Updated description'
    response = logged_in_client.get(f'{BASE}/devices/')
    assert len(response.get_json()['payload']) == 1


def test_reregistration_preserves_image_when_not_provided(app, logged_in_client):
    logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB', 'image': 'ios-xe-17.9.bin'})
    logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB', 'mac': 'aa:bb:cc:dd:ee:ff'})

    with app.app_context():
        d = get_session().get(Device, 'FJC2517X0AB')
        assert d.image == 'ios-xe-17.9.bin'


def test_reregistration_updates_image_when_provided(app, logged_in_client):
    logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB', 'image': 'ios-xe-17.9.bin'})
    logged_in_client.post(f'{BASE}/devices/', json={'serial': 'FJC2517X0AB', 'image': 'ios-xe-17.12.bin'})

    with app.app_context():
        d = get_session().get(Device, 'FJC2517X0AB')
        assert d.image == 'ios-xe-17.12.bin'


# GET /api/v1/devices/<serial>

def test_get_device_returns_401_when_not_logged_in(client, device):
    response = client.get(f'{BASE}/devices/{device.serial}')
    assert response.status_code == 401


def test_get_device_returns_404_when_not_found(logged_in_client):
    response = logged_in_client.get(f'{BASE}/devices/NOSUCHSERIAL')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_found'


def test_get_device_returns_device_payload(logged_in_client, device):
    response = logged_in_client.get(f'{BASE}/devices/{device.serial}')
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['serial'] == device.serial
    assert payload['mac'] == device.mac


# PUT /api/v1/devices/<serial>

def test_edit_device_returns_401_when_not_logged_in(client, device):
    response = client.put(f'{BASE}/devices/{device.serial}', json={'description': 'Updated'})
    assert response.status_code == 401


def test_edit_device_returns_404_when_not_found(logged_in_client):
    response = logged_in_client.put(f'{BASE}/devices/NOSUCHSERIAL', json={'description': 'Updated'})
    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_found'


def test_edit_device_does_not_create_a_new_device(app, logged_in_client):
    logged_in_client.put(f'{BASE}/devices/NOSUCHSERIAL', json={'description': 'Updated'})
    with app.app_context():
        assert get_session().get(Device, 'NOSUCHSERIAL') is None


def test_edit_device_updates_fields(app, logged_in_client, device):
    response = logged_in_client.put(f'{BASE}/devices/{device.serial}', json={
        'mac': '11:22:33:44:55:66',
        'description': 'Updated description',
        'image': 'ios-xe-17.12.bin',
        'config_file': 'edge.cfg',
    })
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['mac'] == '11:22:33:44:55:66'
    assert payload['description'] == 'Updated description'
    assert payload['image'] == 'ios-xe-17.12.bin'
    assert payload['config_file'] == 'edge.cfg'

    with app.app_context():
        d = get_session().get(Device, device.serial)
        assert d.mac == '11:22:33:44:55:66'
        assert d.description == 'Updated description'
        assert d.image == 'ios-xe-17.12.bin'
        assert d.config_file == 'edge.cfg'


def test_edit_device_clears_fields_omitted_from_body(app, logged_in_client, device):
    """PUT replaces the editable fields wholesale, same as the rest of this
    API's PUT routes (e.g. settings/users) — it isn't a partial PATCH."""
    logged_in_client.put(f'{BASE}/devices/{device.serial}', json={})
    with app.app_context():
        d = get_session().get(Device, device.serial)
        assert d.mac is None
        assert d.description is None


def test_edit_device_does_not_change_serial(logged_in_client, device):
    response = logged_in_client.put(f'{BASE}/devices/{device.serial}', json={'description': 'Updated'})
    assert response.get_json()['payload']['serial'] == device.serial


# DELETE /api/v1/devices/<serial>

def test_delete_device_returns_401_when_not_logged_in(client, device):
    response = client.delete(f'{BASE}/devices/{device.serial}')
    assert response.status_code == 401


def test_delete_device_returns_404_when_not_found(logged_in_client):
    response = logged_in_client.delete(f'{BASE}/devices/NOSUCHSERIAL')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'device_not_found'


def test_delete_device_returns_409_when_active_session_exists(logged_in_client, active_session, device):
    response = logged_in_client.delete(f'{BASE}/devices/{device.serial}')
    assert response.status_code == 409
    assert response.get_json()['error'] == 'active_session_exists'


def test_delete_device_returns_200_on_success(logged_in_client, device):
    response = logged_in_client.delete(f'{BASE}/devices/{device.serial}')
    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_delete_device_removes_from_db(app, logged_in_client, device):
    logged_in_client.delete(f'{BASE}/devices/{device.serial}')
    with app.app_context():
        assert get_session().get(Device, device.serial) is None


def test_delete_device_clears_device_logs(app, logged_in_client, device):
    with app.app_context():
        session = get_session()
        session.add(DeviceLogEntry(serial=device.serial, source='script', message='provisioning started'))
        session.commit()

    response = logged_in_client.delete(f'{BASE}/devices/{device.serial}')
    assert response.status_code == 200

    with app.app_context():
        remaining = get_session().query(DeviceLogEntry).filter_by(serial=device.serial).all()
    assert remaining == []


# GET /api/v1/devices/sessions

def test_list_sessions_returns_401_when_not_logged_in(client):
    response = client.get(f'{BASE}/devices/sessions')
    assert response.status_code == 401


def test_list_sessions_returns_empty_list_when_none_active(logged_in_client):
    response = logged_in_client.get(f'{BASE}/devices/sessions')
    assert response.status_code == 200
    assert response.get_json()['payload'] == []


def test_list_sessions_returns_active_sessions(logged_in_client, active_session):
    response = logged_in_client.get(f'{BASE}/devices/sessions')
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert len(payload) == 1
    assert payload[0]['serial'] == active_session.serial
    assert payload[0]['state'] == 'lease_approved'


# GET /api/v1/devices/sessions/<serial>

def test_get_session_returns_401_when_not_logged_in(client, active_session):
    response = client.get(f'{BASE}/devices/sessions/{active_session.serial}')
    assert response.status_code == 401


def test_get_session_returns_404_when_not_found(logged_in_client):
    response = logged_in_client.get(f'{BASE}/devices/sessions/NOSUCHSERIAL')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'session_not_found'


def test_get_session_returns_session_payload(logged_in_client, active_session):
    response = logged_in_client.get(f'{BASE}/devices/sessions/{active_session.serial}')
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['serial'] == active_session.serial
    assert payload['ip'] == active_session.ip
    assert payload['state'] == 'lease_approved'


def test_get_session_payload_reports_not_stale_when_recently_seen(logged_in_client, active_session):
    payload = logged_in_client.get(f'{BASE}/devices/sessions/{active_session.serial}').get_json()['payload']
    assert payload['stale'] is False


def test_get_session_payload_reports_stale_after_timeout(logged_in_client, stale_session):
    payload = logged_in_client.get(f'{BASE}/devices/sessions/{stale_session.serial}').get_json()['payload']
    assert payload['stale'] is True


# DELETE /api/v1/devices/sessions/<serial> — see docs/database.md, "Stale sessions"

def test_cancel_session_returns_401_when_not_logged_in(client, stale_session):
    response = client.delete(f'{BASE}/devices/sessions/{stale_session.serial}')
    assert response.status_code == 401


def test_cancel_session_returns_404_when_not_found(logged_in_client):
    response = logged_in_client.delete(f'{BASE}/devices/sessions/NOSUCHSERIAL')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'session_not_found'


def test_cancel_session_returns_409_when_not_yet_stale(app, logged_in_client, active_session):
    response = logged_in_client.delete(f'{BASE}/devices/sessions/{active_session.serial}')
    assert response.status_code == 409
    assert response.get_json()['error'] == 'session_not_stale'

    with app.app_context():
        assert get_session().get(ProvisioningSession, active_session.serial) is not None


def test_cancel_session_returns_200_when_stale(logged_in_client, stale_session):
    response = logged_in_client.delete(f'{BASE}/devices/sessions/{stale_session.serial}')
    assert response.status_code == 200


def test_cancel_session_removes_the_session(app, logged_in_client, stale_session):
    logged_in_client.delete(f'{BASE}/devices/sessions/{stale_session.serial}')
    with app.app_context():
        assert get_session().get(ProvisioningSession, stale_session.serial) is None


def test_cancel_session_writes_a_provisioning_log_entry(app, logged_in_client, stale_session):
    logged_in_client.delete(f'{BASE}/devices/sessions/{stale_session.serial}')
    with app.app_context():
        entry = get_session().query(ProvisioningLog).filter_by(serial=stale_session.serial).one()
    assert entry.event == 'provision_cancelled'
    assert entry.ip == stale_session.ip


def test_cancel_session_retains_device_logs(app, logged_in_client, stale_session):
    with app.app_context():
        session = get_session()
        session.add(DeviceLogEntry(serial=stale_session.serial, source='script', message='stuck here'))
        session.commit()

    logged_in_client.delete(f'{BASE}/devices/sessions/{stale_session.serial}')

    with app.app_context():
        remaining = get_session().query(DeviceLogEntry).filter_by(serial=stale_session.serial).all()
    assert len(remaining) == 1
