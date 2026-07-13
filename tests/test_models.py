from datetime import datetime, timezone

from drawbridge.models import utcnow_iso


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
