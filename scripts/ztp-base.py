#!/usr/bin/env python
"""Base ZTP script served to IOS XE devices by Drawbridge.

Alpha stub only: exercises the fetch/callback contract end-to-end so it can
be verified without real hardware. It does not verify image or config
hashes, or push any configuration. That logic is deliberately deferred until
this can be tested against real IOS XE devices — see docs/decisions.md and
alpha.md step 5.
"""

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

# Must match the host/port devices reach Drawbridge on (see Option 67 in
# kea/kea-dhcp4.conf). This script runs on the device (IOS XE Guestshell),
# not the server, so it can't read the server's DRAWBRIDGE_PORT env var
# (see docs/deployment.md) — update this constant by hand, along with
# kea-dhcp4.conf's Option 67 URL, if the server's port ever changes from
# the default. See docs/decisions.md.
DRAWBRIDGE_HOST = '192.168.100.1'
DRAWBRIDGE_PORT = 8080

# Hand-maintained, same posture as DRAWBRIDGE_HOST above — must be set to
# this deployment's actual Drawbridge TLS cert (or its issuing CA) before
# this script is used against a device with `cli` present. A self-signed
# cert is a valid trust anchor on its own; no real CA hierarchy required.
# Used two ways: fed to `crypto pki authenticate` on C9200CX (no network
# stack of its own, see below), and as ssl's `cadata` on other real
# platforms (which do have their own network stack).
DRAWBRIDGE_CA_CERT_PEM = None

C9200CX_PLATFORM = 'C9200CX'
TRUSTPOINT_NAME = 'DRAWBRIDGE-CA'

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


def get_platform():
    """Best-effort platform/PID lookup via 'show version' — parsed, not
    probed: a genuine network failure and "no network stack at all" must
    not look the same (see the three-way dispatch in request_provisioning/
    report_status below). Returns None on IOS XE if the field isn't found,
    and a local-testing fallback string otherwise. Field name unverified
    against real hardware output — same posture as other C9200CX-dependent
    parsing in this file, see docs/decisions.md."""
    try:
        import cli
    except ImportError:
        return os.environ.get('ZTP_TEST_PLATFORM', 'TEST-PLATFORM')

    output = cli.execute('show version')
    match = re.search(r'[Mm]odel [Nn]umber\s*:\s*(\S+)', output)
    if match:
        return match.group(1)
    return None


def _ensure_c9200cx_trustpoint(cli):
    """Imports DRAWBRIDGE_CA_CERT_PEM into an IOS XE trustpoint so `copy
    https://...` can validate Drawbridge's certificate instead of blocking
    on an interactive accept/reject prompt it has no TTY to answer, or
    failing outright — IOS XE's HTTPS client does not silently accept an
    unverifiable cert. Idempotent: re-authenticating an already-trusted
    cert is harmless. Must run before any `copy https://` call on this
    platform.
    """
    cli.execute('crypto pki trustpoint {0}'.format(TRUSTPOINT_NAME))
    cli.execute('enrollment terminal')
    cli.execute('revocation-check none')
    cli.execute('exit')
    cli.execute('crypto pki authenticate {0}'.format(TRUSTPOINT_NAME))
    cli.execute(DRAWBRIDGE_CA_CERT_PEM)
    cli.execute('quit')
    cli.execute('yes')


def _unverified_test_ssl_context():
    # ponytail: unverified TLS — only reachable when `cli` isn't importable
    # (local-testing tooling, never real hardware), so there's no real cert
    # to pin against. Real devices always go through one of the two
    # branches above that do verify.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def request_provisioning(serial, platform):
    """Phones home to Drawbridge before doing anything else. Any device on
    the provisioning VLAN can reach this — the allowlist check on the
    server side is the actual gate, not the caller's identity (see
    docs/decisions.md). GET with query-string params, not POST with a JSON
    body: IOS XE's 'copy' primitive (the only network I/O available on
    C9200CX, per the isolation note below) can't attach a request body,
    only a URL and a destination file. Returns the parsed decision dict, or
    None if denied/unreachable.
    """
    url = 'https://{0}:{1}/api/v1/provision-request?serial={2}'.format(
        DRAWBRIDGE_HOST, DRAWBRIDGE_PORT, urllib.parse.quote(serial))

    try:
        import cli
    except ImportError:
        cli = None

    if cli is not None and platform == C9200CX_PLATFORM:
        # C9200CX's Guestshell has no usable network stack of its own —
        # direct socket calls fail. Fetch via IOS XE's own 'copy' primitive
        # instead (see docs/decisions.md "C9200CX network stack isolation").
        _ensure_c9200cx_trustpoint(cli)
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

    if cli is not None:
        # Guestshell has its own network stack on this platform — verify
        # Drawbridge's cert directly rather than delegating to IOS XE's copy.
        context = ssl.create_default_context(cadata=DRAWBRIDGE_CA_CERT_PEM)
        try:
            with urllib.request.urlopen(url, timeout=10, context=context) as response:
                return json.load(response)
        except urllib.error.HTTPError:
            return None  # e.g. 404 device_not_found — denied, fail closed

    try:
        with urllib.request.urlopen(url, timeout=10, context=_unverified_test_ssl_context()) as response:
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


def report_status(payload, platform):
    """Reports completion to Drawbridge. On C9200CX, Guestshell is isolated
    from the device's own network stack, so the report is done by writing
    the payload to flash and having IOS XE's own 'copy' command (via
    cli.execute) issue the HTTP request — see docs/decisions.md. Other real
    platforms and local testing use direct HTTP requests instead.
    """
    url = 'https://{0}:{1}/api/v1/provision-complete'.format(DRAWBRIDGE_HOST, DRAWBRIDGE_PORT)

    try:
        import cli
    except ImportError:
        cli = None

    if cli is not None and platform == C9200CX_PLATFORM:
        _ensure_c9200cx_trustpoint(cli)
        with open(STATUS_LOCAL_PATH, 'w') as f:
            json.dump(payload, f)
        # IOS XE's 'copy' to an HTTP(S) destination issues a PUT (see decisions.md).
        cli.execute('copy {0} {1}'.format(STATUS_FLASH_PATH, url))
        return

    if cli is not None:
        context = ssl.create_default_context(cadata=DRAWBRIDGE_CA_CERT_PEM)
    else:
        context = _unverified_test_ssl_context()

    body = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='PUT', headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(request, timeout=10, context=context)


def main():
    serial = get_serial()
    if serial is None:
        return

    platform = get_platform()

    decision = request_provisioning(serial, platform)
    if decision is None or not decision.get('success'):
        return  # denied or unreachable — exit cleanly, no completion callback

    payload = build_status_payload(serial)
    report_status(payload, platform)


if __name__ == '__main__':
    main()
