"""Unit tests for drawbridge/auth_backends.py — no Flask app needed, these
exercise the extracted verification logic directly against User rows."""
from werkzeug.security import generate_password_hash

from drawbridge.auth_backends import CREDENTIAL_BACKENDS, LOCAL
from drawbridge.models import User

PASSWORD = 'test-password-123'


def _local_user(**kwargs) -> User:
    defaults = dict(username='operator', role='operator', auth_source='local')
    defaults.update(kwargs)
    return User(**defaults)


def test_verify_login_succeeds_with_correct_password():
    user = _local_user(password_hash=generate_password_hash(PASSWORD))
    assert LOCAL.verify_login(user, PASSWORD) is True


def test_verify_login_fails_with_wrong_password():
    user = _local_user(password_hash=generate_password_hash(PASSWORD))
    assert LOCAL.verify_login(user, 'wrong') is False


def test_verify_login_fails_when_password_hash_is_none():
    user = _local_user(password_hash=None)
    assert LOCAL.verify_login(user, PASSWORD) is False


def test_verify_claim_succeeds_for_unclaimed_local_account_with_matching_token():
    user = _local_user(password_hash=None, claim_token='tok-123')
    assert LOCAL.verify_claim(user, 'tok-123') is True


def test_verify_claim_fails_with_wrong_token():
    user = _local_user(password_hash=None, claim_token='tok-123')
    assert LOCAL.verify_claim(user, 'wrong-token') is False


def test_verify_claim_fails_when_already_claimed():
    user = _local_user(password_hash=generate_password_hash(PASSWORD), claim_token=None)
    assert LOCAL.verify_claim(user, 'anything') is False


def test_verify_claim_fails_for_non_local_account():
    user = User(username='saml-op', role='operator', auth_source='saml', password_hash=None, claim_token='tok-123')
    assert LOCAL.verify_claim(user, 'tok-123') is False


def test_credential_backends_registry_dispatches_by_auth_source():
    assert CREDENTIAL_BACKENDS['local'] is LOCAL


def test_credential_backends_registry_has_no_entry_for_saml():
    # SAML doesn't submit credentials through /auth/login — this absence is
    # what makes login() reject a SAML-only account without special-casing it.
    assert 'saml' not in CREDENTIAL_BACKENDS
