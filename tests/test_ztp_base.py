"""Self-check for scripts/ztp-base.py's phone-home gate and HTTPS transport
dispatch. Loaded by file path (importlib) since the filename isn't a valid
module name (hyphen) — it's a standalone script shipped to devices, not
part of the drawbridge package.
"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / 'scripts' / 'ztp-base.py'


@pytest.fixture()
def ztp_base(monkeypatch):
    monkeypatch.setenv('ZTP_TEST_SERIAL', 'TEST-SERIAL-0001')
    spec = importlib.util.spec_from_file_location('ztp_base', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fake_cli(monkeypatch):
    """Simulates `import cli` succeeding (as it does inside IOS XE
    Guestshell). `execute('show version')` returns canned output carrying
    both the serial and model-number fields; any other command is recorded
    on `.calls` and returns ''."""
    module = MagicMock()
    module.calls = []

    def execute(command):
        module.calls.append(command)
        if command == 'show version':
            return 'Serial Number : TEST-SERIAL-0001\nModel Number  : {0}\n'.format(
                module.platform)
        return ''

    module.execute = MagicMock(side_effect=execute)
    module.platform = 'OTHER-PLATFORM'
    monkeypatch.setitem(__import__('sys').modules, 'cli', module)
    return module


def _urlopen_returning(payload):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(payload).encode()
    response.__iter__.return_value = iter([])
    # json.load(response) calls response.read()
    return MagicMock(return_value=response)


def test_main_reports_status_when_approved(ztp_base):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})) as urlopen, \
         patch.object(ztp_base, 'report_status') as report_status:
        ztp_base.main()

    assert urlopen.called
    report_status.assert_called_once()


def test_main_does_not_report_status_when_denied(ztp_base):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': False})), \
         patch.object(ztp_base, 'report_status') as report_status:
        ztp_base.main()

    report_status.assert_not_called()


def test_get_platform_without_cli_uses_test_env_fallback(ztp_base, monkeypatch):
    monkeypatch.setenv('ZTP_TEST_PLATFORM', 'TEST-PLATFORM')
    assert ztp_base.get_platform() == 'TEST-PLATFORM'


def test_request_provisioning_uses_https_and_versioned_api_path(ztp_base):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})) as urlopen:
        ztp_base.request_provisioning('TEST-SERIAL-0001', 'TEST-PLATFORM')

    called_url = urlopen.call_args[0][0]
    assert called_url.startswith('https://{0}:{1}/api/v1/provision-request'.format(
        ztp_base.DRAWBRIDGE_HOST, ztp_base.DRAWBRIDGE_PORT))


def test_report_status_uses_https_and_versioned_api_path(ztp_base):
    with patch('urllib.request.urlopen') as urlopen:
        ztp_base.report_status({'serial': 'TEST-SERIAL-0001'}, 'TEST-PLATFORM')

    request_obj = urlopen.call_args[0][0]
    assert request_obj.full_url == 'https://{0}:{1}/api/v1/provision-complete'.format(
        ztp_base.DRAWBRIDGE_HOST, ztp_base.DRAWBRIDGE_PORT)


def test_log_to_server_uses_https_and_versioned_api_path(ztp_base):
    with patch('urllib.request.urlopen') as urlopen:
        ztp_base.log_to_server('TEST-SERIAL-0001', 'hello', 'TEST-PLATFORM')

    request_obj = urlopen.call_args[0][0]
    assert request_obj.full_url == 'https://{0}:{1}/api/v1/device-logs'.format(
        ztp_base.DRAWBRIDGE_HOST, ztp_base.DRAWBRIDGE_PORT)
    body = json.loads(request_obj.data)
    assert body == {'serial': 'TEST-SERIAL-0001', 'message': 'hello'}


def test_main_calls_log_to_server_at_start_request_and_completion_when_approved(ztp_base):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})), \
         patch.object(ztp_base, 'report_status'), \
         patch.object(ztp_base, 'log_to_server') as log_to_server:
        ztp_base.main()

    assert log_to_server.call_count == 3
    messages = [call.args[1] for call in log_to_server.call_args_list]
    assert messages[0] == 'provisioning started'
    assert messages[1] == 'provision-request: approved'
    assert messages[2] == 'provisioning complete'


def test_main_calls_log_to_server_at_start_and_request_only_when_denied(ztp_base):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': False})), \
         patch.object(ztp_base, 'report_status') as report_status, \
         patch.object(ztp_base, 'log_to_server') as log_to_server:
        ztp_base.main()

    report_status.assert_not_called()
    assert log_to_server.call_count == 2
    messages = [call.args[1] for call in log_to_server.call_args_list]
    assert messages[0] == 'provisioning started'
    assert messages[1] == 'provision-request: denied/unreachable'


def test_c9200cx_imports_trustpoint_before_copy(ztp_base, fake_cli, monkeypatch):
    fake_cli.platform = ztp_base.C9200CX_PLATFORM
    # Simulates an operator who has actually filled in the hand-maintained
    # constant — DRAWBRIDGE_CA_CERT_PEM defaults to None, which cli.execute()
    # can't take a real command string from.
    monkeypatch.setattr(ztp_base, 'DRAWBRIDGE_CA_CERT_PEM', 'FAKE-PEM-BODY')

    # No /bootflash/provision-request.json in the test sandbox — falls
    # through to the documented fail-closed None, same as a real denial.
    result = ztp_base.request_provisioning('TEST-SERIAL-0001', ztp_base.C9200CX_PLATFORM)

    assert result is None
    trustpoint_call = next(i for i, c in enumerate(fake_cli.calls) if c.startswith('crypto pki trustpoint'))
    copy_call = next(i for i, c in enumerate(fake_cli.calls) if c.startswith('copy https://'))
    assert trustpoint_call < copy_call


def test_other_platform_with_cli_verifies_via_cadata_not_copy(ztp_base, fake_cli):
    fake_cli.platform = 'OTHER-PLATFORM'

    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})), \
         patch('ssl.create_default_context') as create_default_context:
        ztp_base.request_provisioning('TEST-SERIAL-0001', 'OTHER-PLATFORM')

    create_default_context.assert_called_once_with(cadata=ztp_base.DRAWBRIDGE_CA_CERT_PEM)
    assert not any(c.startswith('copy ') for c in fake_cli.calls)
