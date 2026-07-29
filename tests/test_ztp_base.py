"""Self-check for scripts/ztp_script.py's phone-home gate, HTTPS transport
dispatch, image/config install, cleanup, and C9200CX trustpoint setup.
Loaded by file path (importlib) since the filename isn't a valid module name
(hyphen) — it's a standalone script shipped to devices, not part of the
drawbridge package.

scripts/ztp_script.py assumes `cli` is always importable (real Guestshell
only, no local-testing fallback — see its own docstring), so `fake_cli`
below is injected into sys.modules before the module ever loads, standing
in for Guestshell's real one. See tests/test_ztp_mock.py for the same
general request/response contract exercised via tests/ztp_mock.py's
`requests`-based simulation instead, with no cli/C9200CX involvement at all.
"""
import importlib.util
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / 'scripts' / 'ztp_script.py'


@pytest.fixture()
def fake_cli(monkeypatch):
    """Simulates `import cli` succeeding (as it does inside IOS XE
    Guestshell). `cli('show version'/'show interfaces vlan 1'/'sh ip int
    vlan 1')` returns canned output in _get_device_info()'s expected
    formats. `execute(...)` and `configurep(...)` record every command on
    `.calls` and return '' by default — a test that wants a fatal CLI
    failure overrides `.side_effect` directly (e.g.
    `fake_cli.execute.side_effect = fake_cli.CLIError('boom')`)."""
    module = MagicMock()
    module.calls = []
    module.serial = 'TEST-SERIAL-0001'
    module.platform = 'OTHER-PLATFORM'
    # A real Exception subclass, not a MagicMock attribute — `except
    # cli.CLIError:` requires a real BaseException subclass to even be
    # syntactically valid once an exception is actually raised.
    module.CLIError = type('CLIError', (Exception,), {})

    def execute(command):
        module.calls.append(command)
        return ''

    def cli(command):
        module.calls.append(command)
        if command == 'show version':
            return (
                'System serial number : {0}\n'
                'Model Number          : {1}\n'
                'Version 17.9.1\n'
            ).format(module.serial.replace('-', ''), module.platform)
        if command == 'show interfaces vlan 1':
            return ('Vlan1 is up, line protocol is up\n'
                    '  Hardware is EtherSVI, address is 001b.0c12.3456 (bia 001b.0c12.3456)\n')
        if command == 'sh ip int vlan 1':
            return 'Vlan1 is up, line protocol is up\n  Internet address is 192.168.100.50/24\n'
        return ''

    def configurep(commands):
        module.calls.extend(commands if isinstance(commands, list) else [commands])
        return ''

    module.execute = MagicMock(side_effect=execute)
    module.cli = MagicMock(side_effect=cli)
    module.configurep = MagicMock(side_effect=configurep)
    monkeypatch.setitem(sys.modules, 'cli', module)
    return module


@pytest.fixture()
def ztp_script_mock(fake_cli, monkeypatch):
    # fake_cli listed as a dependency (not just used by tests that also
    # request it directly) so it's always in sys.modules before this runs -
    # scripts/ztp_script.py does `import cli` at module level now, so loading
    # it below fails immediately without a fake already in place, not just
    # whenever the test body later happens to call into the module.
    #
    # logging.FileHandler is patched out here (not per-test) because every
    # main() run hits it via _setup_logger, which writes to a hardcoded
    # on-device path (/bootflash/guest-share/...) that doesn't exist in this
    # test sandbox - real Guestshell is the only place that path is valid.
    # Swapped for NullHandler rather than a MagicMock: logging's internals
    # (e.g. comparing a record's level against hdlr.level) need a real
    # Handler, not a mock whose attributes are themselves auto-mocked.
    monkeypatch.setattr(logging, 'FileHandler', lambda *a, **k: logging.NullHandler())

    spec = importlib.util.spec_from_file_location('ztp_base', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _urlopen_returning(payload):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(payload).encode()
    response.__iter__.return_value = iter([])
    # json.load(response) calls response.read()
    return MagicMock(return_value=response)


def _set_device(module, serial='TEST-SERIAL-0001', model='OTHER-PLATFORM', version='17.9.1'):
    """Stands in for main()'s DEVICE/LOGGER construction: request_provisioning/
    report_complete/log_to_server now read the module-global DEVICE instead
    of taking serial/platform arguments, and log_to_server() also reads the
    module-global LOGGER (main() always sets both up before any of these
    are ever called), so exercising them directly (without going through
    main()) requires setting both up first. A plain logger, not
    module._setup_logger() — that writes a FileHandler under
    /bootflash/guest-share/, which doesn't exist off-device."""
    module.DEVICE = module.Device(serial=serial, model=model, mac='00:1b:0c:12:34:56',
                                   ip='192.168.100.50', version=version)
    module.LOGGER = logging.getLogger('ztp-test')
    return module.DEVICE


# _get_device_info

def test_get_device_info_parses_show_version_output(ztp_script_mock, fake_cli):
    fake_cli.serial = 'FJC2517X0AB'
    fake_cli.platform = 'C9200CX-12P-2X2G'

    model, serial, version, ip, mac = ztp_script_mock._get_device_info()

    assert serial == 'FJC2517X0AB'
    assert model == 'C9200CX-12P-2X2G'
    assert version == '17.9.1'
    assert ip == '192.168.100.50'
    assert mac == '00:1b:0c:12:34:56'


def test_get_device_info_raises_cli_error_when_cli_call_fails(ztp_script_mock, fake_cli):
    fake_cli.cli.side_effect = fake_cli.CLIError('device unreachable')

    with pytest.raises(fake_cli.CLIError):
        ztp_script_mock._get_device_info()


# General contract (non-C9200CX path) — fake_cli.platform defaults to
# 'OTHER-PLATFORM', so these exercise ztp_script.py's direct-urllib branch.

def test_main_reports_completion_when_approved(ztp_script_mock):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})) as urlopen, \
         patch.object(ztp_script_mock, 'report_complete') as report_complete:
        ztp_script_mock.main()

    assert urlopen.called
    report_complete.assert_called_once()


def test_main_does_not_report_completion_when_denied(ztp_script_mock):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': False})), \
         patch.object(ztp_script_mock, 'report_complete') as report_complete:
        ztp_script_mock.main()

    report_complete.assert_not_called()


def test_request_provisioning_uses_https_and_versioned_api_path(ztp_script_mock):
    _set_device(ztp_script_mock)
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})) as urlopen:
        ztp_script_mock.request_provisioning()

    called_url = urlopen.call_args[0][0]
    assert called_url.startswith('https://{0}:{1}/api/v1/provision-request'.format(
        ztp_script_mock.DRAWBRIDGE_HOST, ztp_script_mock.DRAWBRIDGE_PORT))
    assert 'serial=TEST-SERIAL-0001' in called_url
    assert 'version=17.9.1' in called_url


def test_report_complete_uses_https_and_versioned_api_path(ztp_script_mock):
    _set_device(ztp_script_mock)
    with patch('urllib.request.urlopen') as urlopen:
        ztp_script_mock.report_complete({'serial': 'TEST-SERIAL-0001'})

    request_obj = urlopen.call_args[0][0]
    assert request_obj.full_url == 'https://{0}:{1}/api/v1/provision-complete'.format(
        ztp_script_mock.DRAWBRIDGE_HOST, ztp_script_mock.DRAWBRIDGE_PORT)


def test_report_device_facts_uses_https_and_versioned_api_path(ztp_script_mock):
    _set_device(ztp_script_mock)
    with patch('urllib.request.urlopen') as urlopen:
        ztp_script_mock.report_device_facts()

    request_obj = urlopen.call_args[0][0]
    assert request_obj.full_url == 'https://{0}:{1}/api/v1/provision-request/facts'.format(
        ztp_script_mock.DRAWBRIDGE_HOST, ztp_script_mock.DRAWBRIDGE_PORT)
    body = json.loads(request_obj.data)
    assert body == ztp_script_mock.DEVICE.to_dict()


def test_log_to_server_uses_https_and_versioned_api_path(ztp_script_mock):
    _set_device(ztp_script_mock, serial='TEST-SERIAL-0001')
    with patch('urllib.request.urlopen') as urlopen:
        ztp_script_mock.log_to_server('hello')

    request_obj = urlopen.call_args[0][0]
    assert request_obj.full_url == 'https://{0}:{1}/api/v1/device-logs'.format(
        ztp_script_mock.DRAWBRIDGE_HOST, ztp_script_mock.DRAWBRIDGE_PORT)
    body = json.loads(request_obj.data)
    assert body == {'serial': 'TEST-SERIAL-0001', 'message': 'hello'}


# report_new_state — regression: this used to build a payload and never
# send it at all.

def test_report_new_state_sends_serial_message_and_state(ztp_script_mock):
    _set_device(ztp_script_mock, serial='TEST-SERIAL-0001')
    with patch('urllib.request.urlopen') as urlopen:
        ztp_script_mock.report_new_state('downloading')

    request_obj = urlopen.call_args[0][0]
    assert request_obj.full_url == 'https://{0}:{1}/api/v1/device-logs'.format(
        ztp_script_mock.DRAWBRIDGE_HOST, ztp_script_mock.DRAWBRIDGE_PORT)
    body = json.loads(request_obj.data)
    assert body['serial'] == 'TEST-SERIAL-0001'
    assert body['state'] == 'downloading'
    assert 'downloading' in body['message']


def test_main_calls_log_to_server_at_start_request_and_completion_when_approved(ztp_script_mock):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, 'report_device_facts'), \
         patch.object(ztp_script_mock, 'log_to_server') as log_to_server:
        ztp_script_mock.main()

    assert log_to_server.call_count == 4
    messages = [call.args[0] for call in log_to_server.call_args_list]
    assert messages[0] == 'Provisioning started. Requesting permission from Drawbridge'
    assert messages[1] == 'Provision request: approved'
    assert messages[2] == 'Reported device facts to Drawbridge'
    assert messages[3] == 'Provisioning complete'


def test_main_reports_device_facts_when_approved(ztp_script_mock):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, 'report_device_facts') as report_device_facts:
        ztp_script_mock.main()

    report_device_facts.assert_called_once()


def test_main_does_not_report_device_facts_when_denied(ztp_script_mock):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': False})), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, 'report_device_facts') as report_device_facts:
        ztp_script_mock.main()

    report_device_facts.assert_not_called()


def test_main_calls_log_to_server_at_start_and_request_only_when_denied(ztp_script_mock):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': False})), \
         patch.object(ztp_script_mock, 'report_complete') as report_complete, \
         patch.object(ztp_script_mock, 'log_to_server') as log_to_server:
        ztp_script_mock.main()

    report_complete.assert_not_called()
    assert log_to_server.call_count == 2
    messages = [call.args[0] for call in log_to_server.call_args_list]
    assert messages[0] == 'Provisioning started. Requesting permission from Drawbridge'
    assert messages[1] == 'Provision request: denied/unreachable'


def test_other_platform_with_cli_verifies_via_cadata_not_copy(ztp_script_mock, fake_cli):
    _set_device(ztp_script_mock, model='OTHER-PLATFORM')

    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})), \
         patch('ssl.create_default_context') as create_default_context:
        ztp_script_mock.request_provisioning()

    create_default_context.assert_called_once_with(cadata=ztp_script_mock.DRAWBRIDGE_CA_CERT_PEM)
    assert not any(c.startswith('copy ') for c in fake_cli.calls)


# Image/config install — see docs/decisions.md, "Version-based image
# mapping". Unverified without real hardware which exact install/apply
# commands are correct; these tests only assert the fetch->act ordering and
# state reporting, not the specific IOS command semantics.

def test_main_installs_image_when_payload_includes_one(ztp_script_mock):
    decision = {'success': True, 'payload': {'image': 'cat9k-17.9.1.bin'}}
    with patch('urllib.request.urlopen', _urlopen_returning(decision)), \
         patch.object(ztp_script_mock, 'report_device_facts'), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, '_install_image') as install_image, \
         patch.object(ztp_script_mock, '_apply_config') as apply_config:
        ztp_script_mock.main()

    install_image.assert_called_once_with('cat9k-17.9.1.bin')
    apply_config.assert_not_called()


def test_main_applies_config_when_payload_includes_one(ztp_script_mock):
    decision = {'success': True, 'payload': {'config_file': 'spine.cfg'}}
    with patch('urllib.request.urlopen', _urlopen_returning(decision)), \
         patch.object(ztp_script_mock, 'report_device_facts'), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, '_install_image') as install_image, \
         patch.object(ztp_script_mock, '_apply_config') as apply_config:
        ztp_script_mock.main()

    apply_config.assert_called_once_with('spine.cfg')
    install_image.assert_not_called()


def test_main_skips_image_and_config_when_payload_omits_them(ztp_script_mock):
    decision = {'success': True, 'payload': {}}
    with patch('urllib.request.urlopen', _urlopen_returning(decision)), \
         patch.object(ztp_script_mock, 'report_device_facts'), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, '_install_image') as install_image, \
         patch.object(ztp_script_mock, '_apply_config') as apply_config:
        ztp_script_mock.main()

    install_image.assert_not_called()
    apply_config.assert_not_called()


def test_install_image_fetches_to_flash_then_activates(ztp_script_mock, fake_cli):
    _set_device(ztp_script_mock, model=ztp_script_mock.C9200CX_PLATFORM)
    with patch.object(ztp_script_mock, 'report_new_state'), \
         patch.object(ztp_script_mock, 'log_to_server'):
        ztp_script_mock._install_image('cat9k-17.9.1.bin')

    fetch_call = next(c for c in fake_cli.calls if c.startswith('copy https://'))
    install_call = next(c for c in fake_cli.calls if c.startswith('install add'))
    assert 'files/images/cat9k-17.9.1.bin' in fetch_call
    assert fake_cli.calls.index(fetch_call) < fake_cli.calls.index(install_call)
    assert 'cat9k-17.9.1.bin' in install_call
    assert 'cat9k-17.9.1.bin' in ztp_script_mock._fetched_flash_files


def test_apply_config_fetches_to_flash_then_copies_to_running_config(ztp_script_mock, fake_cli):
    _set_device(ztp_script_mock, model=ztp_script_mock.C9200CX_PLATFORM)
    with patch.object(ztp_script_mock, 'report_new_state'), \
         patch.object(ztp_script_mock, 'log_to_server'):
        ztp_script_mock._apply_config('spine.cfg')

    fetch_call = next(c for c in fake_cli.calls if c.startswith('copy https://'))
    apply_call = next(c for c in fake_cli.calls if 'running-config' in c)
    assert 'files/configs/spine.cfg' in fetch_call
    assert fake_cli.calls.index(fetch_call) < fake_cli.calls.index(apply_call)
    assert 'spine.cfg' in ztp_script_mock._fetched_flash_files


# Fatal CLI error handling — "the script should always catch Cisco CLI
# module errors and print the exact error... fail gracefully"

def test_main_reports_failure_on_fatal_cli_error_during_provisioning(ztp_script_mock, fake_cli):
    decision = {'success': True, 'payload': {'image': 'cat9k-17.9.1.bin'}}
    with patch('urllib.request.urlopen', _urlopen_returning(decision)), \
         patch.object(ztp_script_mock, 'report_device_facts'), \
         patch.object(ztp_script_mock, '_install_image', side_effect=fake_cli.CLIError('flash full')), \
         patch.object(ztp_script_mock, 'report_complete') as report_complete:
        ztp_script_mock.main()  # must not raise - caught and handled

    report_complete.assert_called_once()
    call_payload = report_complete.call_args[0][0]
    assert call_payload['event'] == 'provision_failed'
    assert 'flash full' in call_payload['detail']


def test_main_still_wipes_device_on_fatal_cli_error(ztp_script_mock, fake_cli):
    decision = {'success': True, 'payload': {'image': 'cat9k-17.9.1.bin'}}
    with patch('urllib.request.urlopen', _urlopen_returning(decision)), \
         patch.object(ztp_script_mock, 'report_device_facts'), \
         patch.object(ztp_script_mock, '_install_image', side_effect=fake_cli.CLIError('flash full')), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, 'wipe_device') as wipe_device:
        ztp_script_mock.main()

    wipe_device.assert_called_once()


def test_main_still_wipes_device_when_approved_and_successful(ztp_script_mock):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})), \
         patch.object(ztp_script_mock, 'report_device_facts'), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, 'wipe_device') as wipe_device:
        ztp_script_mock.main()

    wipe_device.assert_called_once()


def test_main_still_wipes_device_when_denied(ztp_script_mock):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': False})), \
         patch.object(ztp_script_mock, 'wipe_device') as wipe_device:
        ztp_script_mock.main()

    wipe_device.assert_called_once()


def test_main_calls_wipe_device_when_no_serial_could_be_parsed(ztp_script_mock, fake_cli):
    # No 'System serial number' line at all -> _get_device_info's regex
    # can't match -> serial is None.
    fake_cli.cli.side_effect = lambda command: 'no useful output' if command == 'show version' else ''

    with patch.object(ztp_script_mock, 'log_to_server'), \
         patch.object(ztp_script_mock, 'wipe_device') as wipe_device:
        ztp_script_mock.main()

    wipe_device.assert_called_once()  # still called explicitly from the no-serial branch


# wipe_device()

def test_wipe_device_is_a_noop_when_device_was_never_set(ztp_script_mock):
    with patch('os.remove') as remove:
        ztp_script_mock.wipe_device()

    remove.assert_not_called()


def test_wipe_device_removes_known_bootflash_files(ztp_script_mock):
    _set_device(ztp_script_mock, model='OTHER-PLATFORM')
    with patch('os.remove') as remove:
        ztp_script_mock.wipe_device()

    removed = {call.args[0] for call in remove.call_args_list}
    assert '/bootflash/guest-share/status.json' in removed
    assert '/bootflash/guest-share/facts.json' in removed
    assert '/bootflash/guest-share/state.json' in removed
    assert '/bootflash/guest-share/provision-request.json' in removed


def test_wipe_device_removes_fetched_flash_files(ztp_script_mock):
    _set_device(ztp_script_mock, model='OTHER-PLATFORM')
    ztp_script_mock._fetched_flash_files.append('cat9k-17.9.1.bin')
    with patch('os.remove') as remove:
        ztp_script_mock.wipe_device()

    removed = {call.args[0] for call in remove.call_args_list}
    assert '/bootflash/guest-share/cat9k-17.9.1.bin' in removed


def test_wipe_device_survives_individual_removal_failures(ztp_script_mock):
    _set_device(ztp_script_mock, model='OTHER-PLATFORM')
    with patch('os.remove', side_effect=OSError('not found')) as remove:
        ztp_script_mock.wipe_device()  # must not raise

    assert remove.call_count > 1  # kept going after the first failure


def test_wipe_device_reverts_trustpoint_for_c9200cx(ztp_script_mock, fake_cli):
    _set_device(ztp_script_mock, model=ztp_script_mock.C9200CX_PLATFORM)
    with patch('os.remove'):
        ztp_script_mock.wipe_device()

    assert any('no crypto pki trustpoint' in c for c in fake_cli.calls)


def test_wipe_device_does_not_touch_trustpoint_for_other_platforms(ztp_script_mock, fake_cli):
    _set_device(ztp_script_mock, model='OTHER-PLATFORM')
    with patch('os.remove'):
        ztp_script_mock.wipe_device()

    assert not any('crypto pki' in c for c in fake_cli.calls)


# C9200CX trustpoint setup

# A real (if throwaway) cert — _pem_to_ios_cert_chain_block() base64-decodes
# it for real, so this needs valid PEM/base64, not just a placeholder string
# (regression: an earlier version of this test used 'FAKE-PEM-BODY', which
# stopped working the moment the cert content was actually decoded instead
# of just relayed as a literal string to `crypto pki authenticate`'s
# terminal-paste prompt — see _ensure_c9200cx_trustpoint()'s docstring for
# why that whole approach was replaced).
_FAKE_CA_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIBODCB66ADAgECAhR46MvnVcLerfN9Cg1RkBTaDZxwbjAFBgMrZXAwEjEQMA4G
A1UEAwwHdGVzdC1jYTAeFw0yNjA3MjgwOTU2MDRaFw0yNjA3MjkwOTU2MDRaMBIx
EDAOBgNVBAMMB3Rlc3QtY2EwKjAFBgMrZXADIQCq4tNuv3UK58fQX9KkXkQ/Catb
a2VCuluIWotDFc9QCaNTMFEwHQYDVR0OBBYEFCkHCgopqvCz7les/FtqYxiEIZED
MB8GA1UdIwQYMBaAFCkHCgopqvCz7les/FtqYxiEIZEDMA8GA1UdEwEB/wQFMAMB
Af8wBQYDK2VwA0EAn9RZYwZNQdhb9Hyu2jOjos/R/A+HLxiCoq6Idgls0v+tIXsE
XvraTZDSRjRK0XTlSxTXMyIM7VDz2NWrXz+GDg==
-----END CERTIFICATE-----"""


def test_ensure_c9200cx_trustpoint_installs_the_configured_cert(ztp_script_mock, fake_cli, monkeypatch):
    monkeypatch.setattr(ztp_script_mock, 'DRAWBRIDGE_CA_CERT_PEM', _FAKE_CA_CERT_PEM)

    ztp_script_mock._ensure_c9200cx_trustpoint()

    trustpoint_call = next(c for c in fake_cli.calls if c.startswith('crypto pki trustpoint'))
    cert_chain_call = next(c for c in fake_cli.calls if c.startswith('crypto pki certificate chain'))
    secure_trustpoint_call = next(c for c in fake_cli.calls if c.startswith('ip http client secure-trustpoint'))
    assert (
        fake_cli.calls.index(trustpoint_call)
        < fake_cli.calls.index(cert_chain_call)
        < fake_cli.calls.index(secure_trustpoint_call)
    )

    # No crypto pki authenticate at all — see _ensure_c9200cx_trustpoint()'s
    # docstring for why (its interactive fingerprint-accept prompt can
    # never be answered through cli.configurep()'s blocking call model).
    # The cert is installed directly as its own `crypto pki certificate
    # chain` config block instead.
    assert not any('crypto pki authenticate' in c for c in fake_cli.calls)
    assert 'crypto pki certificate chain DRAWBRIDGE-CA' in cert_chain_call
    assert 'certificate ca 01' in cert_chain_call
    assert 'quit' in cert_chain_call


def test_ensure_c9200cx_trustpoint_cert_chain_block_round_trips_the_der(ztp_script_mock):
    import base64

    block = ztp_script_mock._pem_to_ios_cert_chain_block(_FAKE_CA_CERT_PEM)

    der_from_block = bytes.fromhex(''.join(line.strip() for line in block.splitlines()))

    body = ''.join(line for line in _FAKE_CA_CERT_PEM.strip().splitlines() if not line.startswith('-----'))
    der_from_pem = base64.b64decode(body)

    assert der_from_block == der_from_pem


def test_request_provisioning_on_c9200cx_does_not_set_up_its_own_trustpoint(ztp_script_mock, fake_cli):
    """Regression: _ensure_c9200cx_trustpoint() used to run inside
    request_provisioning() (and _put_json()) on every single call — it's
    main()'s job now, once, before any network call. request_provisioning()
    called on its own must not redo it.
    """
    _set_device(ztp_script_mock, model=ztp_script_mock.C9200CX_PLATFORM)

    # No /bootflash/provision-request.json in the test sandbox - falls
    # through to the documented fail-closed None, same as a real denial.
    # log_to_server() itself is mocked out: on this OSError path it writes
    # DEVICE.is_c9200cx's own /bootflash/guest-share/ file too, which this
    # test sandbox doesn't have either — same posture as
    # test_main_sets_up_trustpoint_exactly_once_for_c9200cx above.
    with patch.object(ztp_script_mock, 'log_to_server'):
        result = ztp_script_mock.request_provisioning()

    assert result is None
    assert not any(c.startswith('crypto pki') for c in fake_cli.calls)
    assert any(c.startswith('copy https://') for c in fake_cli.calls)


def test_main_sets_up_trustpoint_exactly_once_for_c9200cx(ztp_script_mock, fake_cli):
    """The actual bug this fixes: a full main() run makes several calls
    (log_to_server x2-3, request_provisioning, maybe report_complete), each
    of which used to re-run the whole trustpoint import. It must run
    exactly once for the whole run instead. main()'s collaborators are
    mocked out here (not exercised for real — that's covered by the other
    tests above) specifically to isolate main()'s own orchestration of
    _ensure_c9200cx_trustpoint from request_provisioning/_put_json's
    C9200CX branches, which need a real /bootflash/ filesystem this test
    sandbox doesn't have.
    """
    fake_cli.platform = ztp_script_mock.C9200CX_PLATFORM

    with patch.object(ztp_script_mock, '_ensure_c9200cx_trustpoint') as trustpoint, \
         patch.object(ztp_script_mock, 'log_to_server'), \
         patch.object(ztp_script_mock, 'request_provisioning', return_value={'success': True}), \
         patch.object(ztp_script_mock, 'report_device_facts'), \
         patch.object(ztp_script_mock, 'report_complete'), \
         patch.object(ztp_script_mock, 'wipe_device'):
        ztp_script_mock.main()

    trustpoint.assert_called_once()


def test_main_sets_up_trustpoint_before_any_other_call_for_c9200cx(ztp_script_mock, fake_cli):
    fake_cli.platform = ztp_script_mock.C9200CX_PLATFORM
    call_order = []

    with patch.object(ztp_script_mock, '_ensure_c9200cx_trustpoint', side_effect=lambda: call_order.append('trustpoint')), \
         patch.object(ztp_script_mock, 'log_to_server', side_effect=lambda *a, **k: call_order.append('log_to_server')), \
         patch.object(ztp_script_mock, 'request_provisioning',
                      side_effect=lambda *a, **k: call_order.append('request_provisioning') or {'success': True}), \
         patch.object(ztp_script_mock, 'report_device_facts', side_effect=lambda *a, **k: call_order.append('report_device_facts')), \
         patch.object(ztp_script_mock, 'report_complete', side_effect=lambda *a, **k: call_order.append('report_complete')), \
         patch.object(ztp_script_mock, 'wipe_device'):
        ztp_script_mock.main()

    assert call_order[0] == 'trustpoint'
    assert len(call_order) > 1  # sanity: main() did go on to call the rest


def test_main_does_not_set_up_trustpoint_for_other_platforms(ztp_script_mock, fake_cli):
    fake_cli.platform = 'OTHER-PLATFORM'

    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})):
        ztp_script_mock.main()

    assert not any(c.startswith('crypto pki') for c in fake_cli.calls)
