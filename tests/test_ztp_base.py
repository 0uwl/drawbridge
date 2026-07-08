"""Self-check for scripts/ztp-base.py's new phone-home gate. Loaded by file
path (importlib) since the filename isn't a valid module name (hyphen) —
it's a standalone script shipped to devices, not part of the drawbridge
package.
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
