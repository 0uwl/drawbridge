from drawbridge.db import get_session
from drawbridge.models import KeaLogEntry
from drawbridge.queries import add_kea_log_entry

BASE = '/api/v1'


# GET /api/v1/kea-logs

def test_get_kea_logs_returns_401_when_not_logged_in(client):
    response = client.get(f'{BASE}/kea-logs')
    assert response.status_code == 401


def test_get_kea_logs_returns_200_for_operator(app, logged_in_client):
    with app.app_context():
        add_kea_log_entry(get_session(), message='DHCPDISCOVER received')
        get_session().commit()

    response = logged_in_client.get(f'{BASE}/kea-logs')
    assert response.status_code == 200
    messages = {e['message'] for e in response.get_json()['payload']}
    assert 'DHCPDISCOVER received' in messages


def test_get_kea_logs_after_id_returns_only_newer_rows(app, logged_in_client):
    with app.app_context():
        session = get_session()
        first = add_kea_log_entry(session, message='a')
        session.commit()
        second = add_kea_log_entry(session, message='b')
        session.commit()
        first_id = first.id

    response = logged_in_client.get(f'{BASE}/kea-logs', query_string={'after_id': first_id})
    payload = response.get_json()['payload']
    assert [e['id'] for e in payload] == [second.id]


# POST /api/v1/kea-logs

def test_post_kea_log_persists_row(client, app):
    response = client.post(f'{BASE}/kea-logs', json={'message': 'DHCPACK on 192.168.100.42'})
    assert response.status_code == 200

    with app.app_context():
        rows = get_session().query(KeaLogEntry).all()
    assert len(rows) == 1
    assert rows[0].message == 'DHCPACK on 192.168.100.42'


def test_post_kea_log_missing_message_returns_422(client):
    response = client.post(f'{BASE}/kea-logs', json={})
    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_parameter'


def test_post_kea_log_does_not_require_login(client):
    """Open route, no auth decorator — the rsyslog forwarder has no
    operator session to authenticate with (same posture as POST /device-logs)."""
    response = client.post(f'{BASE}/kea-logs', json={'message': 'hi'})
    assert response.status_code == 200


def test_post_kea_log_accepts_body_without_content_type(client, app):
    response = client.post(f'{BASE}/kea-logs', data=b'{"message": "hi"}')
    assert response.status_code == 200
