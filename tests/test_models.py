from datetime import datetime, timedelta, timezone

from drawbridge.models import ProvisioningSession, SESSION_STALE_AFTER_MINUTES, utcnow_iso


def test_utcnow_iso_is_timezone_aware_and_parseable():
    parsed = datetime.fromisoformat(utcnow_iso())

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(None)


def test_utcnow_iso_always_includes_fractional_seconds():
    # isoformat() drops the microsecond field when it's exactly 0; the
    # log-retention purge query in queries.py compares ts strings directly,
    # so a fixed-width format is required for that comparison to stay
    # chronologically correct.
    _, _, time_part = utcnow_iso().partition('T')

    assert '.' in time_part


def _session_last_seen(minutes_ago: float) -> ProvisioningSession:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec='microseconds')
    return ProvisioningSession(serial='SN1', state='lease_approved', last_seen_at=ts)


def test_is_stale_false_when_seen_recently():
    assert _session_last_seen(1).is_stale() is False


def test_is_stale_false_just_under_the_threshold():
    assert _session_last_seen(SESSION_STALE_AFTER_MINUTES - 1).is_stale() is False


def test_is_stale_true_once_past_the_threshold():
    assert _session_last_seen(SESSION_STALE_AFTER_MINUTES + 1).is_stale() is True
