"""Requests-based mock ZTP client.

Simulates the same phone-home / completion-callback / log-push contract
scripts/ztp_script.py implements against a real Drawbridge instance, without
any Cisco IOS XE `cli` dependency — that module now assumes it always runs
inside real Guestshell (see its own docstring), so it can no longer double
as a local testing tool. This file exists purely to exercise the same
server-side contract using the `requests` library, for the test suite
(tests/test_ztp_mock.py) and for manual smoke-testing against a live
dev.sh/Drawbridge instance:

    python tests/ztp_mock.py --serial TEST-SERIAL-0001

Not a deployable artifact — never served to a device, never touches `cli`.
"""

import argparse

import requests

DRAWBRIDGE_HOST = '192.168.100.1'
DRAWBRIDGE_PORT = 8080
DRAWBRIDGE_BASE_URL = f'https://{DRAWBRIDGE_HOST}:{DRAWBRIDGE_PORT}/api/v1'

# Drawbridge's default cert is self-signed (see drawbridge/tls.py) — False
# mirrors scripts/ztp_script.py's cadata-pinned trust without needing to
# plumb the actual cert PEM through here too. Never used against a real
# device; only ever points at a local/dev Drawbridge instance.
VERIFY = False


def request_provisioning(serial, base_url=DRAWBRIDGE_BASE_URL, verify=VERIFY):
    """Mirrors scripts/ztp_script.py's request_provisioning() for non-C9200CX
    platforms: a GET with the serial as a query param, returning the parsed
    decision dict or None if denied/unreachable."""
    response = requests.get(
        f'{base_url}/provision-request', params={'serial': serial}, verify=verify, timeout=10,
    )
    if response.status_code != 200:
        return None
    return response.json()


def build_status_payload(serial):
    return {
        'serial': serial,
        'event': 'provision_complete',
        'image': None,
        'config_file': None,
        'detail': 'tests/ztp_mock.py — contract simulation only, no provisioning performed',
    }


def report_status(payload, base_url=DRAWBRIDGE_BASE_URL, verify=VERIFY):
    """Mirrors scripts/ztp_script.py's report_status()."""
    requests.put(f'{base_url}/provision-complete', json=payload, verify=verify, timeout=10)


def log_to_server(serial, message, base_url=DRAWBRIDGE_BASE_URL, verify=VERIFY):
    """Mirrors scripts/ztp_script.py's log_to_server()."""
    requests.put(
        f'{base_url}/device-logs', json={'serial': serial, 'message': message}, verify=verify, timeout=10,
    )


def main(serial, base_url=DRAWBRIDGE_BASE_URL, verify=VERIFY):
    """Mirrors scripts/ztp_script.py's main() — same call sequence and
    log-message contract, non-C9200CX path only (no trustpoint/copy
    machinery to simulate here, see the module docstring)."""
    log_to_server(serial, 'provisioning started', base_url, verify)

    decision = request_provisioning(serial, base_url, verify)
    approved = decision is not None and decision.get('success')
    log_to_server(serial, 'provision-request: ' + ('approved' if approved else 'denied/unreachable'), base_url, verify)
    if not approved:
        return

    payload = build_status_payload(serial)
    report_status(payload, base_url, verify)
    log_to_server(serial, 'provisioning complete', base_url, verify)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True, help='Serial number to phone home with')
    parser.add_argument('--base-url', default=DRAWBRIDGE_BASE_URL, help=f'Default: {DRAWBRIDGE_BASE_URL}')
    args = parser.parse_args()
    main(args.serial, args.base_url)
