"""Unit tests for log_poller._handle_line's parsing/correlation logic.
Does not test the FIFO-open/blocking-loop or s6 wiring — infra, not logic,
same split beta.md draws for the supervisor layer (manual smoke test
instead, see docs/deployment.md)."""
from drawbridge.db import get_session
from drawbridge.log_poller import _handle_line
from drawbridge.models import DeviceLogEntry, ProvisioningSession


def test_handle_line_correlates_serial_by_source_ip(app):
    with app.app_context():
        session = get_session()
        session.add(ProvisioningSession(serial='SN1', ip='192.168.100.15', state='lease_approved'))
        session.commit()

        _handle_line(app.extensions['db_session_factory'], '192.168.100.15 %LINK-3-UPDOWN: Interface Gi1/0/1')

        rows = session.query(DeviceLogEntry).all()
    assert len(rows) == 1
    assert rows[0].serial == 'SN1'
    assert rows[0].source == 'syslog'
    assert rows[0].message == '%LINK-3-UPDOWN: Interface Gi1/0/1'


def test_handle_line_leaves_serial_null_when_no_session_matches(app):
    with app.app_context():
        session = get_session()

        _handle_line(app.extensions['db_session_factory'], '10.0.0.99 no session for this ip')

        rows = session.query(DeviceLogEntry).all()
    assert len(rows) == 1
    assert rows[0].serial is None
    assert rows[0].message == 'no session for this ip'


def test_handle_line_without_a_leading_ip_keeps_whole_line_as_message(app):
    with app.app_context():
        session = get_session()

        _handle_line(app.extensions['db_session_factory'], 'malformed-line-no-space')

        rows = session.query(DeviceLogEntry).all()
    assert len(rows) == 1
    assert rows[0].serial is None
    assert rows[0].message == 'malformed-line-no-space'
