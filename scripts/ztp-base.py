#!/usr/bin/env python
"""Base ZTP script served to IOS XE devices by Drawbridge.

Alpha stub only: exercises the fetch/callback contract end-to-end so it can
be verified without real hardware. It does not validate certs, verify image
or config hashes, or push any configuration. That logic is deliberately
deferred until this can be tested against real IOS XE devices — see
docs/decisions.md and alpha.md step 5.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

# Must match the host/port devices reach Drawbridge on (see Option 67 in
# kea/kea-dhcp4.conf and the Drawbridge deployment config).
DRAWBRIDGE_HOST = '192.168.100.1'
DRAWBRIDGE_PORT = 8080

STATUS_FILENAME = 'status.json'
STATUS_FLASH_PATH = 'flash:' + STATUS_FILENAME
# Guestshell's bind-mounted view of flash: — see docs/decisions.md
# "C9200CX network stack isolation".
STATUS_LOCAL_PATH = '/bootflash/' + STATUS_FILENAME

PROVISION_REQUEST_FILENAME = 'provision-request.json'
PROVISION_REQUEST_FLASH_PATH = 'flash:' + PROVISION_REQUEST_FILENAME
PROVISION_REQUEST_LOCAL_PATH = '/bootflash/' + PROVISION_REQUEST_FILENAME


def get_serial():
    """Best-effort serial lookup via 'show version'. Returns None on IOS XE
    or if the field isn't found, and the local-testing fallback string
    otherwise."""
    try:
        import cli
    except ImportError:
        return os.environ.get('ZTP_TEST_SERIAL', 'TEST-SERIAL-0001')

    output = cli.execute('show version')
    match = re.search(r'[Ss]erial [Nn]umber\s*:\s*(\S+)', output)
    if match:
        return match.group(1)
    return None


def request_provisioning(serial):
    """Phones home to Drawbridge before doing anything else. Any device on
    the provisioning VLAN can reach this — the allowlist check on the
    server side is the actual gate, not the caller's identity (see
    docs/decisions.md). GET with query-string params, not POST with a JSON
    body: IOS XE's 'copy' primitive (the only network I/O available in
    Guestshell, per the C9200CX isolation note above) can't attach a
    request body, only a URL and a destination file. Returns the parsed
    decision dict, or None if denied/unreachable.
    """
    url = 'http://{0}:{1}/api/provision-request?serial={2}'.format(
        DRAWBRIDGE_HOST, DRAWBRIDGE_PORT, urllib.parse.quote(serial))

    try:
        import cli
    except ImportError:
        cli = None

    if cli is not None:
        cli.execute('copy {0} {1}'.format(url, PROVISION_REQUEST_FLASH_PATH))
        # ponytail: copy's behavior on a non-2xx response (e.g. a 404 denial)
        # is unverified without real hardware — treat a missing/unreadable
        # local file the same as a denial (fail closed either way). Revisit
        # once tested against a real C9200CX.
        try:
            with open(PROVISION_REQUEST_LOCAL_PATH) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError:
        return None  # e.g. 404 device_not_found — denied, fail closed


def build_status_payload(serial):
    return {
        'serial': serial,
        'event': 'provision_complete',
        'image': None,
        'config_file': None,
        'detail': 'ztp-base.py stub — contract test only, no provisioning performed',
    }


def report_status(payload):
    """Reports completion to Drawbridge. On real IOS XE hardware, Guestshell
    is isolated from the device's own network stack, so the report is done
    by writing the payload to flash and having IOS XE's own 'copy' command
    (via cli.execute) issue the HTTP request — see docs/decisions.md.
    Outside Guestshell (local testing), falls back to a direct HTTP request.
    """
    url = 'http://{0}:{1}/api/provision-complete'.format(DRAWBRIDGE_HOST, DRAWBRIDGE_PORT)

    try:
        import cli
    except ImportError:
        cli = None

    if cli is not None:
        with open(STATUS_LOCAL_PATH, 'w') as f:
            json.dump(payload, f)
        # IOS XE's 'copy' to an HTTP destination issues a PUT (see decisions.md).
        cli.execute('copy {0} {1}'.format(STATUS_FLASH_PATH, url))
        return

    body = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='PUT', headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(request, timeout=10)


def main():
    serial = get_serial()
    if serial is None:
        return

    decision = request_provisioning(serial)
    if decision is None or not decision.get('success'):
        return  # denied or unreachable — exit cleanly, no completion callback

    payload = build_status_payload(serial)
    report_status(payload)


if __name__ == '__main__':
    main()
