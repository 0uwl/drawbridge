"""Syslog message -> ProvisioningSession state detection. Each vendor gets
its own tuple of (state, keywords) rules, checked in order — first match
wins, so a terminal/specific state (e.g. an error) should sit before a
broader in-progress one within the same vendor's table. Keywords are plain
substrings, matching how these platforms' own logging conventions read (no
regex needed for anything seen so far). Cross-vendor collision isn't a
concern: different platforms' log line vocabularies don't overlap, so
there's no need to detect which vendor a line came from before matching.
"""

# Cisco IOS-XE install/reboot lifecycle - see docs/logging.md.
CISCO_IOS_XE_TRIGGERS = (
    ('rebooting', ('Completed install activate',)),
    ('error', ('OPERATION_ERROR',)),
    ('updating_software', ('Started install add', 'Started install activate')),
)

# Juniper ZTP - add trigger tuples here once wired up; nothing else in this
# codebase needs to change (see docs/logging.md, "Multi-vendor").
JUNIPER_TRIGGERS: tuple = ()

ALL_TRIGGERS = CISCO_IOS_XE_TRIGGERS + JUNIPER_TRIGGERS


def detect_state(message: str) -> str | None:
    """Returns the state a syslog line implies, or None for a routine
    line that isn't a lifecycle event."""
    for state, keywords in ALL_TRIGGERS:
        if any(keyword in message for keyword in keywords):
            return state
    return None
