"""Unit tests for device_events.detect_state — pure logic, no Flask/DB
fixtures needed, same split test_log_poller.py used for _handle_line."""
from drawbridge.device_events import detect_state


def test_completed_install_activate_means_rebooting():
    assert detect_state('%INSTALL-5-INSTALL_COMPLETED_INFO: Completed install activate') == 'rebooting'


def test_operation_error_means_error():
    assert detect_state('%INSTALL-3-INSTALL_STATE_ERROR: OPERATION_ERROR: install failed') == 'error'


def test_started_install_add_means_updating_software():
    assert detect_state('%INSTALL-5-INSTALL_START_INFO: Started install add') == 'updating_software'


def test_started_install_activate_means_updating_software():
    assert detect_state('%INSTALL-5-INSTALL_START_INFO: Started install activate') == 'updating_software'


def test_unrelated_line_returns_none():
    assert detect_state('%LINK-3-UPDOWN: Interface Gi1/0/1, changed state to up') is None
