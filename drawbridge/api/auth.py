from flask import Blueprint, current_app, request
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from drawbridge.auth import limiter
from drawbridge.db import get_session
from drawbridge.models import utcnow_iso
from drawbridge.queries import get_user_by_username
from drawbridge.utils import error_response, success_response

MIN_PASSWORD_LENGTH = 8


# NOTE: URL prefixes are defined and appended to the following routes when this blueprint is registered in main.py.
#       They should not be defined here

def create_blueprint():
    bp = Blueprint(name='auth', import_name= __name__)

    @bp.post('/login')
    @limiter.limit('10 per minute')
    def login():
        data = request.get_json(silent=True) or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return error_response('Username and password are required', 'invalid_request', code=400)

        session = get_session()
        user = get_user_by_username(session, username)

        # Treat "user not found", "SAML-only account" (no password_hash), and
        # "wrong password" identically — no username enumeration via error text.
        if not user or not user.password_hash or not check_password_hash(user.password_hash, password):
            _log_auth_failure('login_failed', username)
            return error_response('Invalid credentials', 'unauthorized', code=401, silent=True)

        if user.must_reset_password:
            # Credentials are valid, but no session is established yet — the
            # caller must complete POST /auth/reset-password first. See
            # docs/authentication.md ("Bootstrap admin password sources").
            return success_response('Password reset required', payload={
                'must_reset_password': True,
                'username': user.username,
            })

        user.last_login_at = utcnow_iso()
        session.commit()

        error = _login_or_error(user)
        if error:
            return error
        return success_response('Logged in', payload=_user_payload(user))

    @bp.post('/reset-password')
    @limiter.limit('5 per minute')
    def reset_password():
        """Completes a forced password reset for an account whose
        must_reset_password flag is set (currently only the bootstrap admin,
        when its initial password came from ADMIN_PASSWORD — see
        docs/authentication.md). Distinct from /change-password: this route
        is unauthenticated by necessity (login() withholds a session while
        the flag is set) and only succeeds when the flag is actually set,
        so it can't be used as a back door around the normal, session-based
        change-password flow for accounts that don't need a reset."""
        data = request.get_json(silent=True) or {}
        username = data.get('username', '').strip()
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')

        if not username or not current_password or not new_password:
            return error_response(
                'Username, current password, and new password are required', 'invalid_request', code=400,
            )

        session = get_session()
        user = get_user_by_username(session, username)

        if (
            not user
            or not user.must_reset_password
            or not user.password_hash
            or not check_password_hash(user.password_hash, current_password)
        ):
            _log_auth_failure('reset_password_failed', username)
            return error_response('Invalid reset request', 'invalid_reset', code=400, silent=True)

        length_error = _validate_new_password(new_password)
        if length_error:
            _log_auth_failure('weak_password', username)
            return error_response(length_error, 'weak_password', code=400, silent=True)

        user.password_hash = generate_password_hash(new_password, method='scrypt')
        user.must_reset_password = False
        user.last_login_at = utcnow_iso()
        session.commit()

        error = _login_or_error(user)
        if error:
            return error
        return success_response('Password reset', payload=_user_payload(user))

    @bp.post('/claim')
    @limiter.limit('5 per minute')
    def claim():
        """First-time password creation for an admin-created local account.
        Unauthenticated by necessity (the account has no password yet), but
        only succeeds against a local account that has never been claimed —
        auth_source='local', password_hash still NULL, and a matching
        claim_token (single-use, minted at account creation or by an
        admin-triggered reset — see docs/authentication.md)."""
        data = request.get_json(silent=True) or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')
        token = data.get('token', '')

        if not username or not password or not token:
            return error_response('Username, password, and token are required', 'invalid_request', code=400)

        session = get_session()
        user = get_user_by_username(session, username)

        if (
            not user
            or user.auth_source != 'local'
            or user.password_hash is not None
            or user.claim_token != token
        ):
            _log_auth_failure('claim_failed', username)
            return error_response('Invalid claim request', 'invalid_claim', code=400, silent=True)

        length_error = _validate_new_password(password)
        if length_error:
            _log_auth_failure('weak_password', username)
            return error_response(length_error, 'weak_password', code=400, silent=True)

        user.password_hash = generate_password_hash(password, method='scrypt')
        user.claim_token = None
        session.commit()
        return success_response('Password set')

    @bp.post('/change-password')
    @login_required
    def change_password():
        data = request.get_json(silent=True) or {}
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')

        if not current_password or not new_password:
            return error_response('Current and new password are required', 'invalid_request', code=400)

        if not current_user.password_hash or not check_password_hash(current_user.password_hash, current_password):
            _log_auth_failure('change_password_failed', current_user.username)
            return error_response('Current password is incorrect', 'invalid_credentials', code=400, silent=True)

        length_error = _validate_new_password(new_password)
        if length_error:
            _log_auth_failure('weak_password', current_user.username)
            return error_response(length_error, 'weak_password', code=400, silent=True)

        session = get_session()
        current_user.password_hash = generate_password_hash(new_password, method='scrypt')
        session.commit()
        return success_response('Password changed')

    @bp.post('/logout')
    @login_required
    def logout():
        logout_user()
        return success_response('Logged out')

    @bp.get('/me')
    @login_required
    def me():
        return success_response('Authenticated', payload=_user_payload(current_user))

    return bp


def _validate_new_password(password: str) -> str | None:
    """Returns an error message if password fails the minimum-length policy, else None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f'Password must be at least {MIN_PASSWORD_LENGTH} characters'
    return None


def _log_auth_failure(event: str, username: str) -> None:
    """Non-silent log line for a failed-auth event — username + event type
    only, never the password. The error_response(..., silent=True) calls
    alongside this intentionally skip logging the client-facing message (to
    keep the response generic and avoid username enumeration); this is the
    server-side signal half of that tradeoff."""
    current_app.logger.warning(f'auth failure: {event} username={username!r}')


def _login_or_error(user):
    """Calls login_user(); returns an error Response if Flask-Login refused
    the session (is_active=False), else None. Nothing sets is_active=False
    today — this only stops the column from lying if something does later."""
    if not login_user(user):
        _log_auth_failure('account_inactive', user.username)
        return error_response('Account is deactivated', 'account_inactive', code=403, silent=True)
    return None


def _user_payload(user):
    return {
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'auth_source': user.auth_source,
    }
