"""Self-check for scripts/ztp-base.py's phone-home gate, HTTPS transport
dispatch, and C9200CX trustpoint setup. Loaded by file path (importlib)
since the filename isn't a valid module name (hyphen) — it's a standalone
script shipped to devices, not part of the drawbridge package.

scripts/ztp-base.py now assumes `cli` is always importable (real Guestshell
only, no local-testing fallback — see its own docstring), so `fake_cli`
below is injected into sys.modules before the module ever loads, standing
in for Guestshell's real one. See tests/test_ztp_mock.py for the same
general request/response contract exercised via tests/ztp_mock.py's
`requests`-based simulation instead, with no cli/C9200CX involvement at all.
"""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / 'scripts' / 'ztp-base.py'


@pytest.fixture()
def fake_cli(monkeypatch):
    """Simulates `import cli` succeeding (as it does inside IOS XE
    Guestshell). `execute('show version')` returns canned output carrying
    both the serial and model-number fields; any other command is recorded
    on `.calls` and returns ''."""
    module = MagicMock()
    module.calls = []
    module.serial = 'TEST-SERIAL-0001'
    module.platform = 'OTHER-PLATFORM'

    def execute(command):
        module.calls.append(command)
        if command == 'show version':
            return 'Serial Number : {0}\nModel Number  : {1}\n'.format(module.serial, module.platform)
        return ''

    module.execute = MagicMock(side_effect=execute)
    monkeypatch.setitem(sys.modules, 'cli', module)
    return module


@pytest.fixture()
def ztp_base(fake_cli):
    # fake_cli listed as a dependency (not just used by tests that also
    # request it directly) so it's always in sys.modules before this runs -
    # scripts/ztp-base.py does `import cli` at module level now, so loading
    # it below fails immediately without a fake already in place, not just
    # whenever the test body later happens to call into the module.
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


# get_serial / get_platform

def test_get_serial_parses_show_version_output(ztp_base, fake_cli):
    fake_cli.serial = 'FJC2517X0AB'
    assert ztp_base.get_serial() == 'FJC2517X0AB'


def test_get_platform_parses_show_version_output(ztp_base, fake_cli):
    fake_cli.platform = ztp_base.C9200CX_PLATFORM
    assert ztp_base.get_platform() == ztp_base.C9200CX_PLATFORM


# General contract (non-C9200CX path) — fake_cli.platform defaults to
# 'OTHER-PLATFORM', so these exercise ztp-base.py's direct-urllib branch.

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


def test_request_provisioning_uses_https_and_versioned_api_path(ztp_base):
    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})) as urlopen:
        ztp_base.request_provisioning('TEST-SERIAL-0001', 'OTHER-PLATFORM')

    called_url = urlopen.call_args[0][0]
    assert called_url.startswith('https://{0}:{1}/api/v1/provision-request'.format(
        ztp_base.DRAWBRIDGE_HOST, ztp_base.DRAWBRIDGE_PORT))


def test_report_status_uses_https_and_versioned_api_path(ztp_base):
    with patch('urllib.request.urlopen') as urlopen:
        ztp_base.report_status({'serial': 'TEST-SERIAL-0001'}, 'OTHER-PLATFORM')

    request_obj = urlopen.call_args[0][0]
    assert request_obj.full_url == 'https://{0}:{1}/api/v1/provision-complete'.format(
        ztp_base.DRAWBRIDGE_HOST, ztp_base.DRAWBRIDGE_PORT)


def test_log_to_server_uses_https_and_versioned_api_path(ztp_base):
    with patch('urllib.request.urlopen') as urlopen:
        ztp_base.log_to_server('TEST-SERIAL-0001', 'hello', 'OTHER-PLATFORM')

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


def test_other_platform_with_cli_verifies_via_cadata_not_copy(ztp_base, fake_cli):
    fake_cli.platform = 'OTHER-PLATFORM'

    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})), \
         patch('ssl.create_default_context') as create_default_context:
        ztp_base.request_provisioning('TEST-SERIAL-0001', 'OTHER-PLATFORM')

    create_default_context.assert_called_once_with(cadata=ztp_base.DRAWBRIDGE_CA_CERT_PEM)
    assert not any(c.startswith('copy ') for c in fake_cli.calls)


# C9200CX trustpoint setup

def test_ensure_c9200cx_trustpoint_authenticates_the_configured_cert(ztp_base, fake_cli, monkeypatch):
    monkeypatch.setattr(ztp_base, 'DRAWBRIDGE_CA_CERT_PEM', 'FAKE-PEM-BODY')

    ztp_base._ensure_c9200cx_trustpoint()

    trustpoint_call = next(i for i, c in enumerate(fake_cli.calls) if c.startswith('crypto pki trustpoint'))
    authenticate_call = next(i for i, c in enumerate(fake_cli.calls) if c.startswith('crypto pki authenticate'))
    assert trustpoint_call < authenticate_call
    assert 'FAKE-PEM-BODY' in fake_cli.calls


def test_request_provisioning_on_c9200cx_does_not_set_up_its_own_trustpoint(ztp_base, fake_cli):
    """Regression: _ensure_c9200cx_trustpoint() used to run inside
    request_provisioning() (and _put_json()) on every single call — it's
    main()'s job now, once, before any network call. request_provisioning()
    called on its own must not redo it.
    """
    # No /bootflash/provision-request.json in the test sandbox - falls
    # through to the documented fail-closed None, same as a real denial.
    result = ztp_base.request_provisioning('TEST-SERIAL-0001', ztp_base.C9200CX_PLATFORM)

    assert result is None
    assert not any(c.startswith('crypto pki') for c in fake_cli.calls)
    assert any(c.startswith('copy https://') for c in fake_cli.calls)


def test_main_sets_up_trustpoint_exactly_once_for_c9200cx(ztp_base, fake_cli):
    """The actual bug this fixes: a full main() run makes several calls
    (log_to_server x2-3, request_provisioning, maybe report_status), each
    of which used to re-run the whole trustpoint import. It must run
    exactly once for the whole run instead. main()'s collaborators are
    mocked out here (not exercised for real — that's covered by the other
    tests above) specifically to isolate main()'s own orchestration of
    _ensure_c9200cx_trustpoint from request_provisioning/_put_json's
    C9200CX branches, which need a real /bootflash/ filesystem this test
    sandbox doesn't have.
    """
    fake_cli.platform = ztp_base.C9200CX_PLATFORM

    with patch.object(ztp_base, '_ensure_c9200cx_trustpoint') as trustpoint, \
         patch.object(ztp_base, 'log_to_server'), \
         patch.object(ztp_base, 'request_provisioning', return_value={'success': True}), \
         patch.object(ztp_base, 'report_status'):
        ztp_base.main()

    trustpoint.assert_called_once()


def test_main_sets_up_trustpoint_before_any_other_call_for_c9200cx(ztp_base, fake_cli):
    fake_cli.platform = ztp_base.C9200CX_PLATFORM
    call_order = []

    with patch.object(ztp_base, '_ensure_c9200cx_trustpoint', side_effect=lambda: call_order.append('trustpoint')), \
         patch.object(ztp_base, 'log_to_server', side_effect=lambda *a, **k: call_order.append('log_to_server')), \
         patch.object(ztp_base, 'request_provisioning',
                      side_effect=lambda *a, **k: call_order.append('request_provisioning') or {'success': True}), \
         patch.object(ztp_base, 'report_status', side_effect=lambda *a, **k: call_order.append('report_status')):
        ztp_base.main()

    assert call_order[0] == 'trustpoint'
    assert len(call_order) > 1  # sanity: main() did go on to call the rest


def test_main_does_not_set_up_trustpoint_for_other_platforms(ztp_base, fake_cli):
    fake_cli.platform = 'OTHER-PLATFORM'

    with patch('urllib.request.urlopen', _urlopen_returning({'success': True})):
        ztp_base.main()

    assert not any(c.startswith('crypto pki') for c in fake_cli.calls)
