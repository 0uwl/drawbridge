#!/usr/bin/env python
"""Base ZTP script served to IOS XE devices by Drawbridge.

Alpha stub only: exercises the fetch/callback contract end-to-end so it can
be verified without real hardware. It does not verify image or config
hashes, or push any configuration. That logic is deliberately deferred until
this can be tested against real IOS XE devices - see docs/decisions.md and
alpha.md step 5.

Runs only on a real IOS XE device (Guestshell) - `cli` is assumed
importable unconditionally, no local-testing fallback. See
tests/ztp_mock.py for a `requests`-based simulation of this same contract,
used by the test suite instead of running this file off-device.
"""

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import sys

import logging
from logging.handlers import SysLogHandler

# Guestshell's cli module raises cli.CLIError on a failed cli()/execute()/
# configurep() call, per Cisco's own Guestshell Python API docs - assumed
# uniform across all three, unverified without real hardware (see
# docs/decisions.md).
import cli # type: ignore

# Must match the host/port devices reach Drawbridge on (see Option 67 in
# kea/kea-dhcp4.conf). This script runs on the device (IOS XE Guestshell),
# not the server, so it can't read the server's DRAWBRIDGE_PORT env var
# (see docs/deployment.md) - update this constant by hand, along with
# kea-dhcp4.conf's Option 67 URL, if the server's port ever changes from
# the default. See docs/decisions.md.
DRAWBRIDGE_HOST = '192.168.100.1'
DRAWBRIDGE_PORT = 8080
DRAWBRIDGE_LOG_PORT = 10514
DRAWBRIDGE_BASE_URL = f'https://{DRAWBRIDGE_HOST}:{DRAWBRIDGE_PORT}/api/v1'
# /files/* is a separate, unversioned Flask blueprint (see docs/api.md) -
# not under /api/v1 like everything else this script calls.
DRAWBRIDGE_FILES_BASE_URL = f'https://{DRAWBRIDGE_HOST}:{DRAWBRIDGE_PORT}/files'

# The following must be set to this deployment's actual Drawbridge TLS cert (or its
# issuing CA) before this script is used. A self-signed cert is a valid trust anchor 
# on its own; no real CA hierarchy required. Used two ways: fed to 
# `crypto pki authenticate` on C9200CX (no network stack of its own, see below), 
# and as ssl's `cadata` on other platforms (which do have their own network stack).
#
# Auto-synced by drawbridge/tls.py after every Drawbridge restart (see
# docs/deployment.md, "TLS") to match whatever cert Drawbridge is actually
# serving - the BEGIN/END markers below mark exactly what gets rewritten;
# nothing else in this file is touched. Remove the markers if you'd rather
# manage this by hand instead (e.g. pointing at an org CA rather than the
# served leaf cert) - Drawbridge skips files missing them.
# --- DRAWBRIDGE_CA_CERT_PEM:BEGIN ---
DRAWBRIDGE_CA_CERT_PEM = None
# --- DRAWBRIDGE_CA_CERT_PEM:END ---

C9200CX_PLATFORM = 'C9200CX'
TRUSTPOINT_NAME = 'DRAWBRIDGE-CA'

STATUS_FILENAME = 'status.json'
FACTS_FILENAME = 'facts.json'
STATE_FILENAME = 'state.json'

PROVISION_REQUEST_FILENAME = 'provision-request.json'
PROVISION_REQUEST_FLASH_PATH = 'flash:guest-share/' + PROVISION_REQUEST_FILENAME
PROVISION_REQUEST_LOCAL_PATH = '/bootflash/guest-share/' + PROVISION_REQUEST_FILENAME

# IOS groups a certificate chain's hex dump into 4-byte (8 hex char) groups,
# 7 groups (28 bytes) per line - matches what `show running-config` renders
# for any trustpoint's stored CA cert.
_IOS_HEX_GROUP_CHARS = 8
_IOS_HEX_GROUPS_PER_LINE = 7

LOGGER: logging.Logger

class Device:
    def __init__(self, serial: str, model: str, mac: str, ip: str, version: str):
        self.serial = serial
        self.model = model
        self.mac = mac
        self.ip = ip
        self.version = version
        self.is_c9200cx = C9200CX_PLATFORM in model

    def to_dict(self):
        return {
            'mac': self.mac,
            'ip': self.ip,
            'model': self.model,
            'version': self.version,
            'serial': self.serial,
        }
DEVICE: Device


def _setup_logger(name: str, log_file_name: str, level=logging.INFO):
    """Setup the logger for the ZTP script. Creates handlers for stdout, file and syslog. 
    Syslog is not used for C9200CX series since they cannot communicate with the device network stack

    Args:
        name (str): The logger name shown in the message
        log_file_name (str): The name of the local log file. Always stored in /bootflash/guest-share
        level (_type_, optional): Logging level of each handler. Defaults to logging.INFO.

    Returns:
        _type_: The newly created logger
    """
    # Create logger with the given name
    new_logger = logging.getLogger(name)
    new_logger.setLevel(level)
    new_logger.propagate = False

    # Formatter for log messages
    formatter = logging.Formatter('[%(asctime)s] [%(name)s]: %(levelname)s - %(message)s', datefmt='%b %d %H:%M:%S')
    stdout_formatter = logging.Formatter('[%(asctime)s] [ZTP Script]: %(levelname)s - %(message)s', datefmt='%b %d %H:%M:%S')

    # StreamHandler for stdout
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(stdout_formatter)

    # FileHandler for writing to a log file
    file_handler = logging.FileHandler(f'/bootflash/guest-share/{log_file_name}')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # SyslogHandler for sending logs to Drawbridge, if platform is not C9200CX
    syslog_handler = SysLogHandler(address=(DRAWBRIDGE_HOST, DRAWBRIDGE_PORT))
    syslog_handler.setLevel(level)
    syslog_handler.setFormatter(formatter)

    # Avoid adding handlers multiple times
    if not new_logger.handlers:
        new_logger.addHandler(stdout_handler)
        new_logger.addHandler(file_handler)
        if DEVICE.is_c9200cx:
            new_logger.addHandler(syslog_handler)

    return new_logger


def _get_device_info():
    """
    Extracts the model number, serial number, ip and software version from Cisco IOS-XE 'show version' output.

    Returns:
        tuple: (model_number, serial_number, software_version)
               If a value is not found, None is returned for that field.
    """
    show_version = cli.cli('show version')
    show_interfaces = cli.cli('show interfaces vlan 1')
    show_ip_on_interface = cli.cli('sh ip int vlan 1')

    model = re.search(r'[Mm]odel [Nn]umber\s*:\s*(C[0-9A-Z\-]+)', show_version)
    serial = re.search(r'[Ss]ystem [Ss]erial [Nn]umber\s*:\s*([A-Z0-9]+)', show_version)
    version = re.search(r'[Vv]ersion\s+(\d+\.\d+\.\d+)', show_version)
    mac = re.search(r'Vlan1[\s\S]+?address is ([0-9a-fA-F.]+)', show_interfaces)
    ip = re.search(r'Internet address is (\d{1,3}(?:\.\d{1,3}){3})', show_ip_on_interface)

    if mac is not None:
        # Remove any dots ('.') from the matched IOS-style MAC (e.g. 001b.0c12.3456)
        mac = mac.group(1).replace('.', '')
        # Split the MAC address into groups of two characters and join with colons
        mac = ':'.join([mac[i:i + 2] for i in range(0, len(mac), 2)])

    return (
        str(model.group(1)) if model else '',
        str(serial.group(1)) if serial else None,
        str(version.group(1)) if version else '',
        str(ip.group(1)) if ip else '',
        str(mac) if mac else '',
    )


def _pem_to_ios_cert_chain_block(pem: str) -> str:
    """Renders a PEM certificate as the `crypto pki certificate chain`
    config block IOS itself uses to persist an authenticated trustpoint's CA
    cert (see `show running-config` on any router with one configured) -
    the exact hex-dump format (4-byte/8-hex-char groups, 7 per line,
    2-space-indented, terminated with `quit`) IOS renders and re-parses
    certs in.

    The `certificate ca <serial>` label below is an arbitrary hex token,
    not the certificate's real X.509 serial number - it only exists to
    distinguish multiple entries within one trustpoint's chain.

    Feeding this block straight into config mode installs the cert as
    already-trusted with no further step needed.
    """
    body = ''.join(line for line in pem.strip().splitlines() if not line.startswith('-----'))
    hex_bytes = base64.b64decode(body).hex().upper()
    groups = [hex_bytes[i:i + _IOS_HEX_GROUP_CHARS] for i in range(0, len(hex_bytes), _IOS_HEX_GROUP_CHARS)]
    lines_per_group = _IOS_HEX_GROUPS_PER_LINE
    hex_lines = [
        '  ' + ' '.join(groups[i:i + lines_per_group])
        for i in range(0, len(groups), lines_per_group)
    ]
    return ' \n '.join([*hex_lines])


def _ensure_c9200cx_trustpoint():
    """Installs DRAWBRIDGE_CA_CERT_PEM as a trusted IOS XE trustpoint on a C9200CX
    so that`copy https://...` can validate Drawbridge's certificate or fail outright
    """
    assert DRAWBRIDGE_CA_CERT_PEM is not None  # synced by drawbridge/tls.py before real use

    command = f"""crypto pki trustpoint {TRUSTPOINT_NAME}
    enrollment terminal
    revocation-check none
    exit
    """
    print(f'> Sending command: {command}')
    cli.configurep(command)

    command = f"""crypto pki certificate chain {TRUSTPOINT_NAME}
    certificate ca 01
    {_pem_to_ios_cert_chain_block(DRAWBRIDGE_CA_CERT_PEM)}
    quit
    """
    print(f'> Sending command: {command}')
    cli.configurep(command)

    command = f'ip http client secure-trustpoint {TRUSTPOINT_NAME}'
    print(f'> Sending command: {command}')
    cli.configurep(command)


def request_provisioning():
    """Phones home to Drawbridge before doing anything else. 
    GET with query-string params. The only network I/O available on the
    C9200CX is IOS XE's 'copy' primitive which can't attach a request body,
    only a URL and a destination file. Returns the parsed decision dict, or
    None if denied/unreachable.
    """
    url = (
        f'{DRAWBRIDGE_BASE_URL}/provision-request'
        f'?serial={urllib.parse.quote(DEVICE.serial)}'
        f'&version={urllib.parse.quote(DEVICE.version)}'
    )

    if DEVICE.is_c9200cx:
        # C9200CX's Guestshell has no usable network stack of its own -
        # direct socket calls fail. Fetch via IOS XE's own 'copy' primitive
        # instead (see docs/decisions.md "C9200CX network stack isolation").
        # The trustpoint this needs is already set up once in main().
        cli.execute(f'copy {url} {PROVISION_REQUEST_FLASH_PATH}')
        # ponytail: copy's behavior on a non-2xx response (e.g. a 404 denial)
        # is unverified without real hardware - treat a missing/unreadable
        # local file the same as a denial (fail closed either way). Revisit
        # once tested against a real C9200CX.
        try:
            with open(PROVISION_REQUEST_LOCAL_PATH) as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            log_to_server(f'Failed to read request result from {PROVISION_REQUEST_LOCAL_PATH}: {e}')
            return None

    # Other platforms: Guestshell has its own network stack - verify
    # Drawbridge's cert directly rather than delegating to IOS XE's copy.
    context = ssl.create_default_context(cadata=DRAWBRIDGE_CA_CERT_PEM)
    try:
        with urllib.request.urlopen(url, timeout=10, context=context) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        log_to_server(f'Failed to request provisioning from {url}: {e}')
        return None  # e.g. 404 device_not_found - denied, fail closed



def build_status_payload():
    return {
        'serial': DEVICE.serial,
        'event': 'provision_complete',
        'image': None,
        'config_file': None,
        'detail': 'ztp_script.py stub - contract test only, no provisioning performed',
    }


def _put_json(url, payload, filename):
    """Shared PUT-JSON transport for report_status/log_to_server - both hit
    Drawbridge with a JSON body and no return value needed On C9200CX, 
    Guestshell is isolated from the device's own network stack, 
    so the report is done by writing the payload to flash and having 
    IOS XE's own 'copy' command (via cli.execute) issue the HTTP request.
    Other platforms use a direct HTTP request instead.
    """
    if DEVICE.is_c9200cx:
        with open('/bootflash/guest-share/' + filename, 'w') as f:
            json.dump(payload, f)
        # IOS XE's 'copy' to an HTTP(S) destination issues a PUT (see decisions.md).
        cli.execute(f'copy flash:/guest-share/{filename} {url}')
        return

    context = ssl.create_default_context(cadata=DRAWBRIDGE_CA_CERT_PEM)
    body = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='PUT', headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(request, timeout=10, context=context)


def report_new_state(state):
    """Reports new provisioning state to Drawbridge - applied directly to
    the active ProvisioningSession, skipping the syslog pattern-matching
    Drawbridge otherwise runs incoming messages through (see
    drawbridge/device_events.py and docs/api.md's PUT /device-logs
    contract). `message` is still required by that endpoint's body shape
    even though `state` is what actually matters here.

    Args:
        state (str): One of drawbridge.models.PROVISIONING_STATES
    """
    url = f'{DRAWBRIDGE_BASE_URL}/device-logs'
    payload = {
        'serial': DEVICE.serial,
        'message': f'State changed to {state}',
        'state': state,
    }
    _put_json(url, payload, STATE_FILENAME)


def report_complete(payload):
    """Reports completion to Drawbridge."""
    url = f'{DRAWBRIDGE_BASE_URL}/provision-complete'
    _put_json(url, payload, STATUS_FILENAME)


def report_device_facts():
    """Reports the device's own facts (model, version, mac, ip - see
    Device.to_dict()) to Drawbridge so they can be recorded on the active
    ProvisioningSession row (see docs/decisions.md, "Facts-first
    provisioning"). Only meaningful once a session exists, so this is called
    from main() after request_provisioning() has already approved the
    device - never before. Same _put_json transport as report_complete/
    log_to_server: on C9200CX, Guestshell can't attach a request body to a
    direct call, so the payload is written to flash and delivered via IOS
    XE's own 'copy' primitive issuing the PUT; other platforms PUT directly.
    """
    url = f'{DRAWBRIDGE_BASE_URL}/provision-request/facts'
    _put_json(url, DEVICE.to_dict(), FACTS_FILENAME)


def log_to_server(message):
    """Reports one log line to Drawbridge's device-log feed
    """
    url = f'{DRAWBRIDGE_BASE_URL}/device-logs'
    LOGGER.info(message)
    _put_json(url, {'serial': DEVICE.serial, 'message': message}, 'devicelog.json')


# Flash filenames fetched this run - wipe_device() removes exactly these,
# never a wildcard directory clean, so a failed/partial run can't delete
# something it didn't itself put there.
_fetched_flash_files = []


def _fetch_to_flash(remote_dir, filename):
    """Downloads a file from Drawbridge's (unversioned) file-serving
    endpoint straight to flash via IOS XE's own `copy` primitive, on every
    platform - not just C9200CX. Unlike request_provisioning()/_put_json(),
    which need Python to parse a JSON response (impossible through `copy`
    on C9200CX, see docs/decisions.md), an image/config fetch never needs
    Python to see the bytes at all - they only need to land in flash for
    `install add`/`copy ... running-config` to use afterward. So there's no
    reason to route this through direct urllib the way the JSON-carrying
    calls do; the copy primitive works the same way on every platform.
    """
    url = f'{DRAWBRIDGE_FILES_BASE_URL}/{remote_dir}/{filename}'
    cli.execute(f'copy {url} flash:')
    _fetched_flash_files.append(filename)


def _install_image(filename):
    """Fetches the assigned image and starts the upgrade.

    Unverified without real hardware (see docs/decisions.md): the exact
    `install add`/`activate`/`commit` sequence, and whether it reboots the
    device on its own or needs an explicit follow-up command.
    """
    report_new_state('downloading')
    log_to_server(f'Fetching image {filename}')
    _fetch_to_flash('images', filename)

    report_new_state('updating_software')
    log_to_server(f'Installing image {filename}')
    cli.execute(f'install add file flash:{filename} activate commit')


def _apply_config(filename):
    """Fetches the assigned config and loads it.

    Unverified without real hardware (see docs/decisions.md): whether
    `copy flash:<file> running-config` is the right semantic here, vs.
    `configure replace` (which discards config not present in the file).
    """
    report_new_state('downloading')
    log_to_server(f'Fetching config {filename}')
    _fetch_to_flash('configs', filename)

    report_new_state('configuring')
    log_to_server(f'Applying config {filename}')
    cli.execute(f'copy flash:{filename} running-config')


def wipe_device():
    """Best-effort cleanup: removes every file this script wrote to the
    device and reverts the C9200CX trustpoint config, so nothing about
    this run's involvement lingers once it's done. Called as the literal
    last thing main() does, in a finally block, regardless of outcome
    (success, failure, or denial) - each step is independently caught so
    one failure (e.g. a file that was never written on a denied run, or a
    trustpoint that was never installed) doesn't skip the rest.
    """
    if 'DEVICE' not in globals():
        return  # failed before Device info could even be read - nothing was written yet

    for filename in (
        PROVISION_REQUEST_FILENAME, STATUS_FILENAME, FACTS_FILENAME, STATE_FILENAME, 'devicelog.json',
        *_fetched_flash_files,
    ):
        try:
            os.remove(f'/bootflash/guest-share/{filename}')
        except OSError as e:
            print(f'wipe_device: could not remove {filename}: {e}')

    if DEVICE.is_c9200cx:
        try:
            cli.configurep(f'no crypto pki trustpoint {TRUSTPOINT_NAME}')
        except cli.CLIError as e:
            print(f'wipe_device: could not remove trustpoint {TRUSTPOINT_NAME}: {e}')


def main():
    global LOGGER, DEVICE

    try:
        model, serial, version, ip, mac = _get_device_info()
    except cli.CLIError as e:
        # Nothing set up yet to log to (LOGGER/DEVICE don't exist) - this is
        # as fatal and as early as a failure can get.
        print(f'Fatal CLI error while reading device info: {e}')
        return

    DEVICE = Device(serial=serial or 'unknown', model=model, version=version, mac=mac, ip=ip)
    LOGGER = _setup_logger(name=f'{DEVICE.serial}-logger', log_file_name=f'{DEVICE.serial}.log')

    if serial is None:
        log_to_server('ZTP script failed. No serial could be parsed from device')
        wipe_device()
        return

    try:
        print()

        if DEVICE.is_c9200cx:
            LOGGER.info(f'Platform is {DEVICE.model}. Manually inserting trustpoint name for TLS')
            _ensure_c9200cx_trustpoint()

        log_to_server('Provisioning started. Requesting permission from Drawbridge')

        decision = request_provisioning()
        approved = decision is not None and decision.get('success')
        log_to_server('Provision request: ' + ('approved' if approved else 'denied/unreachable'))
        if decision is None or not approved:
            return  # denied or unreachable - exit cleanly, no completion callback

        report_device_facts()
        log_to_server('Reported device facts to Drawbridge')

        assigned = decision.get('payload') or {}
        if assigned.get('image'):
            _install_image(assigned['image'])
        if assigned.get('config_file'):
            _apply_config(assigned['config_file'])

        payload = build_status_payload()
        report_complete(payload)
        log_to_server('Provisioning complete')

    except cli.CLIError as e:
        # Catches any fatal CLI failure from this point on (trustpoint
        # setup, image install, config apply, ...) - log the exact error
        # and still try to tell Drawbridge this run failed, rather than
        # leaving it stuck showing the last state the device reported.
        LOGGER.error(f'Fatal CLI error during provisioning: {e}')
        log_to_server(f'Fatal CLI error: {e}')
        try:
            report_complete({
                'serial': DEVICE.serial,
                'event': 'provision_failed',
                'detail': f'Fatal CLI error: {e}',
            })
        except Exception as report_err:
            # e.g. no active session yet if the failure happened before
            # request_provisioning() approved one - nothing more to do.
            log_to_server(f'Failed to report failure to Drawbridge: {report_err}')

    finally:
        # Always runs last, regardless of outcome - see wipe_device()'s
        # own docstring.
        wipe_device()


if __name__ == '__main__':
    main()
