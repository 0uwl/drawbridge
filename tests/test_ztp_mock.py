"""Contract tests for tests/ztp_mock.py — the requests-based simulation of
scripts/ztp-base.py's phone-home/completion/log-push flow, standing in for
mocking scripts/ztp-base.py's own cli-dependent transport when testing the
general request/response contract. See tests/test_ztp_base.py for the
Cisco-specific trustpoint/cli behavior that this file deliberately doesn't
cover — ztp_mock.py has no cli/C9200CX machinery to test.
"""
from unittest.mock import MagicMock, patch

import ztp_mock


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    return response


def test_request_provisioning_uses_https_and_versioned_api_path():
    with patch('requests.get', return_value=_response(200, {'success': True})) as get:
        ztp_mock.request_provisioning('TEST-SERIAL-0001')

    called_url = get.call_args[0][0]
    assert called_url == f'{ztp_mock.DRAWBRIDGE_BASE_URL}/provision-request'
    assert get.call_args.kwargs['params'] == {'serial': 'TEST-SERIAL-0001'}


def test_request_provisioning_returns_decision_on_success():
    with patch('requests.get', return_value=_response(200, {'success': True})):
        result = ztp_mock.request_provisioning('TEST-SERIAL-0001')

    assert result == {'success': True}


def test_request_provisioning_returns_none_on_denial():
    with patch('requests.get', return_value=_response(404)):
        result = ztp_mock.request_provisioning('TEST-SERIAL-0001')

    assert result is None


def test_report_status_uses_https_and_versioned_api_path():
    with patch('requests.put', return_value=_response()) as put:
        ztp_mock.report_status({'serial': 'TEST-SERIAL-0001'})

    assert put.call_args[0][0] == f'{ztp_mock.DRAWBRIDGE_BASE_URL}/provision-complete'
    assert put.call_args.kwargs['json'] == {'serial': 'TEST-SERIAL-0001'}


def test_log_to_server_uses_https_and_versioned_api_path():
    with patch('requests.put', return_value=_response()) as put:
        ztp_mock.log_to_server('TEST-SERIAL-0001', 'hello')

    assert put.call_args[0][0] == f'{ztp_mock.DRAWBRIDGE_BASE_URL}/device-logs'
    assert put.call_args.kwargs['json'] == {'serial': 'TEST-SERIAL-0001', 'message': 'hello'}


def test_main_reports_status_when_approved():
    with patch('requests.get', return_value=_response(200, {'success': True})), \
         patch('requests.put'), \
         patch.object(ztp_mock, 'report_status') as report_status:
        ztp_mock.main('TEST-SERIAL-0001')

    report_status.assert_called_once()


def test_main_does_not_report_status_when_denied():
    with patch('requests.get', return_value=_response(404)), \
         patch('requests.put'), \
         patch.object(ztp_mock, 'report_status') as report_status:
        ztp_mock.main('TEST-SERIAL-0001')

    report_status.assert_not_called()


def test_main_calls_log_to_server_at_start_request_and_completion_when_approved():
    with patch('requests.get', return_value=_response(200, {'success': True})), \
         patch.object(ztp_mock, 'report_status'), \
         patch.object(ztp_mock, 'log_to_server') as log_to_server:
        ztp_mock.main('TEST-SERIAL-0001')

    assert log_to_server.call_count == 3
    messages = [call.args[1] for call in log_to_server.call_args_list]
    assert messages[0] == 'provisioning started'
    assert messages[1] == 'provision-request: approved'
    assert messages[2] == 'provisioning complete'


def test_main_calls_log_to_server_at_start_and_request_only_when_denied():
    with patch('requests.get', return_value=_response(404)), \
         patch.object(ztp_mock, 'report_status') as report_status, \
         patch.object(ztp_mock, 'log_to_server') as log_to_server:
        ztp_mock.main('TEST-SERIAL-0001')

    report_status.assert_not_called()
    assert log_to_server.call_count == 2
    messages = [call.args[1] for call in log_to_server.call_args_list]
    assert messages[0] == 'provisioning started'
    assert messages[1] == 'provision-request: denied/unreachable'
