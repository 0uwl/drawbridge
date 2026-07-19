"""Backends for the credential-submission auth family — methods where a
password (or equivalent) is POSTed directly to Drawbridge and checked
against a User row, as opposed to SAML's redirect/assertion flow (see
drawbridge/saml.py). Local is the only one today; this is the seam a future
LDAP backend plugs into without touching drawbridge/api/auth.py's routes —
see docs/authentication.md.
"""
from typing import Protocol

from werkzeug.security import check_password_hash

from drawbridge.models import User


class CredentialAuthBackend(Protocol):
    source: str

    def verify_login(self, user: User, password: str) -> bool: ...


class LocalAuthBackend:
    """Verifies a submitted password against User.password_hash. Shared by
    /auth/login, /auth/reset-password, and /auth/change-password — each
    route layers its own extra gate (must_reset_password, current-session
    identity) on top of this same check."""
    source = 'local'

    def verify_login(self, user: User, password: str) -> bool:
        return bool(user.password_hash) and check_password_hash(user.password_hash, password)

    def verify_claim(self, user: User, token: str) -> bool:
        return (
            user.auth_source == 'local'
            and user.password_hash is None
            and user.claim_token is not None
            and user.claim_token == token
        )


LOCAL = LocalAuthBackend()

# Keyed on User.auth_source. Only methods that submit a username+password
# through /auth/login belong here — login() doesn't know the user's
# auth_source until after the DB lookup, so this dict is what makes that
# dispatch pluggable. SAML doesn't submit credentials this way (see
# drawbridge/saml.py) and isn't part of this registry.
CREDENTIAL_BACKENDS: dict[str, CredentialAuthBackend] = {'local': LOCAL}
