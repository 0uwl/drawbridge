"""End-to-end golden-path integration test per alpha.md step 8's manual
verification pass: register a device, simulate its ZTP phone-home and
completion report, confirm the log records it, then remove the device.
Each individual step already has focused coverage elsewhere (test_lease_api.py,
test_devices_api.py, test_settings_api.py) — this test instead asserts the
full chain behaves consistently when run in sequence, the way an operator
and a real device would actually exercise it.
"""
from drawbridge.db import get_session
from drawbridge.models import Device, ProvisioningSession

BASE = '/api/v1'
SERIAL = 'FJC2517X0AB'


def test_golden_path_register_provision_log_remove(app, logged_in_admin_client, client):
    # 1. Operator registers the device
    response = logged_in_admin_client.post(f'{BASE}/devices/', json={'serial': SERIAL, 'description': 'test switch'})
    assert response.status_code == 200

    response = logged_in_admin_client.get(f'{BASE}/devices/')
    assert response.status_code == 200
    assert SERIAL in {d['serial'] for d in response.get_json()['payload']}

    # 2. Device phones home (unauthenticated) — approved, session created
    response = client.get(
        f'{BASE}/provision-request',
        query_string={'serial': SERIAL, 'mac': 'aa:bb:cc:dd:ee:ff', 'version': '17.9.1'},
    )
    assert response.status_code == 200
    with app.app_context():
        assert get_session().get(ProvisioningSession, SERIAL) is not None

    # 3. Device reports completion (unauthenticated)
    response = client.put(
        f'{BASE}/provision-complete',
        json={'serial': SERIAL, 'event': 'provision_complete', 'image': 'cat9k.bin', 'config_file': 'base.cfg'},
    )
    assert response.status_code == 200
    with app.app_context():
        assert get_session().get(ProvisioningSession, SERIAL) is None
        # Device allowlist row is untouched by provisioning completion
        assert get_session().get(Device, SERIAL) is not None

    # 4. Operator sees the completed run in the log
    response = logged_in_admin_client.get(f'{BASE}/log')
    assert response.status_code == 200
    entries = response.get_json()['payload']
    assert any(e['serial'] == SERIAL and e['event'] == 'provision_complete' for e in entries)

    # 5. Operator removes the device
    response = logged_in_admin_client.delete(f'{BASE}/devices/{SERIAL}')
    assert response.status_code == 200
    with app.app_context():
        assert get_session().get(Device, SERIAL) is None
