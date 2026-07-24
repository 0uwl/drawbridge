#!/usr/bin/env python
"""Base ZTP script served to IOS XE devices by Drawbridge.

Alpha stub only: exercises the fetch/callback contract end-to-end so it can
be verified without real hardware. It does not verify image or config
hashes, or push any configuration. That logic is deliberately deferred until
this can be tested against real IOS XE devices — see docs/decisions.md and
alpha.md step 5.

Runs only on a real IOS XE device (Guestshell) — `cli` is assumed
importable unconditionally, no local-testing fallback. See
tests/ztp_mock.py for a `requests`-based simulation of this same contract,
used by the test suite instead of running this file off-device.
"""

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

import cli # type: ignore

# Must match the host/port devices reach Drawbridge on (see Option 67 in
# kea/kea-dhcp4.conf). This script runs on the device (IOS XE Guestshell),
# not the server, so it can't read the server's DRAWBRIDGE_PORT env var
# (see docs/deployment.md) — update this constant by hand, along with
# kea-dhcp4.conf's Option 67 URL, if the server's port ever changes from
# the default. See docs/decisions.md.
DRAWBRIDGE_HOST = '192.168.100.1'
DRAWBRIDGE_PORT = 8080
DRAWBRIDGE_BASE_URL = f'https://{DRAWBRIDGE_HOST}:{DRAWBRIDGE_PORT}/api/v1'

# Must be set to this deployment's actual Drawbridge TLS cert (or its
# issuing CA) before this script is used against a device with `cli`
# present. A self-signed cert is a valid trust anchor on its own; no real
# CA hierarchy required. Used two ways: fed to `crypto pki authenticate` on
# C9200CX (no network stack of its own, see below), and as ssl's `cadata`
# on other real platforms (which do have their own network stack).
#
# Auto-synced by drawbridge/tls.py after every Drawbridge restart (see
# docs/deployment.md, "TLS") to match whatever cert Drawbridge is actually
# serving — the BEGIN/END markers below mark exactly what gets rewritten;
# nothing else in this file is touched. Remove the markers if you'd rather
# manage this by hand instead (e.g. pointing at an org CA rather than the
# served leaf cert) — Drawbridge skips files missing them.
# --- DRAWBRIDGE_CA_CERT_PEM:BEGIN ---
DRAWBRIDGE_CA_CERT_PEM = None
# --- DRAWBRIDGE_CA_CERT_PEM:END ---

C9200CX_PLATFORM = 'C9200CX'
TRUSTPOINT_NAME = 'DRAWBRIDGE-CA'

STATUS_FILENAME = 'status.json'

PROVISION_REQUEST_FILENAME = 'provision-request.json'
PROVISION_REQUEST_FLASH_PATH = 'flash:' + PROVISION_REQUEST_FILENAME
PROVISION_REQUEST_LOCAL_PATH = '/bootflash/' + PROVISION_REQUEST_FILENAME


def get_serial():
    """Serial via 'show version'. Returns None if the field isn't found."""
    output = cli.execute('show version')
    match = re.search(r'[Ss]erial [Nn]umber\s*:\s*(\S+)', output)
    if match:
        return match.group(1)
    return None


def get_platform():
    """Platform/PID via 'show version' — parsed, not probed: a genuine
    network failure and "no network stack at all" must not look the same
    (see the two-way dispatch in request_provisioning/report_status
    below). Returns None if the field isn't found. Field name unverified
    against real hardware output — same posture as other C9200CX-dependent
    parsing in this file, see docs/decisions.md."""
    output = cli.execute('show version')
    match = re.search(r'[Mm]odel [Nn]umber\s*:\s*(\S+)', output)
    if match:
        return match.group(1)
    return None


def _ensure_c9200cx_trustpoint():
    """Imports DRAWBRIDGE_CA_CERT_PEM into an IOS XE trustpoint so `copy
    https://...` can validate Drawbridge's certificate instead of blocking
    on an interactive accept/reject prompt it has no TTY to answer, or
    failing outright — IOS XE's HTTPS client does not silently accept an
    unverifiable cert. Called once, from main(), right after the platform
    is confirmed as C9200CX — not per network call: re-authenticating the
    same cert into the same trustpoint on every request_provisioning()/
    report_status()/log_to_server() call is redundant, one-time setup is
    all `copy https://...` ever needs for the rest of the run.
    """
    cli.execute(f'crypto pki trustpoint {TRUSTPOINT_NAME}')
    cli.execute('enrollment terminal')
    cli.execute('revocation-check none')
    cli.execute('exit')
    cli.execute(f'crypto pki authenticate {TRUSTPOINT_NAME}')
    cli.execute(DRAWBRIDGE_CA_CERT_PEM)
    cli.execute('quit')
    cli.execute('yes')


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
    url = f'{DRAWBRIDGE_BASE_URL}/provision-request?serial={urllib.parse.quote(serial)}'

    if platform == C9200CX_PLATFORM:
        # C9200CX's Guestshell has no usable network stack of its own —
        # direct socket calls fail. Fetch via IOS XE's own 'copy' primitive
        # instead (see docs/decisions.md "C9200CX network stack isolation").
        # The trustpoint this needs is already set up once in main().
        cli.execute(f'copy {url} {PROVISION_REQUEST_FLASH_PATH}')
        # ponytail: copy's behavior on a non-2xx response (e.g. a 404 denial)
        # is unverified without real hardware — treat a missing/unreadable
        # local file the same as a denial (fail closed either way). Revisit
        # once tested against a real C9200CX.
        try:
            with open(PROVISION_REQUEST_LOCAL_PATH) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    # Other real platforms: Guestshell has its own network stack — verify
    # Drawbridge's cert directly rather than delegating to IOS XE's copy.
    context = ssl.create_default_context(cadata=DRAWBRIDGE_CA_CERT_PEM)
    try:
        with urllib.request.urlopen(url, timeout=10, context=context) as response:
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


def _put_json(url, payload, platform, filename):
    """Shared PUT-JSON transport for report_status/log_to_server — both hit
    Drawbridge with a JSON body and no return value needed, unlike
    request_provisioning's GET-with-a-return-value shape, so this only
    unifies those two. On C9200CX, Guestshell is isolated from the device's
    own network stack, so the report is done by writing the payload to
    flash and having IOS XE's own 'copy' command (via cli.execute) issue
    the HTTP request — see docs/decisions.md. Other real platforms use a
    direct HTTP request instead.
    """
    if platform == C9200CX_PLATFORM:
        with open('/bootflash/' + filename, 'w') as f:
            json.dump(payload, f)
        # IOS XE's 'copy' to an HTTP(S) destination issues a PUT (see decisions.md).
        cli.execute(f'copy flash:{filename} {url}')
        return

    context = ssl.create_default_context(cadata=DRAWBRIDGE_CA_CERT_PEM)
    body = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='PUT', headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(request, timeout=10, context=context)


def report_status(payload, platform):
    """Reports completion to Drawbridge."""
    url = f'{DRAWBRIDGE_BASE_URL}/provision-complete'
    _put_json(url, payload, platform, STATUS_FILENAME)


def log_to_server(serial, message, platform):
    """Reports one log line to Drawbridge's device-log feed (see
    docs/logging.md) — best-effort, same transport as report_status."""
    url = f'{DRAWBRIDGE_BASE_URL}/device-logs'
    _put_json(url, {'serial': serial, 'message': message}, platform, 'devicelog.json')


def main():
    serial = get_serial()
    if serial is None:
        return

    platform = get_platform()
    if platform == C9200CX_PLATFORM:
        _ensure_c9200cx_trustpoint()

    log_to_server(serial, 'provisioning started', platform)

    decision = request_provisioning(serial, platform)
    approved = decision is not None and decision.get('success')
    log_to_server(serial, 'provision-request: ' + ('approved' if approved else 'denied/unreachable'), platform)
    if not approved:
        return  # denied or unreachable — exit cleanly, no completion callback

    payload = build_status_payload(serial)
    report_status(payload, platform)
    log_to_server(serial, 'provisioning complete', platform)


if __name__ == '__main__':
    main()
