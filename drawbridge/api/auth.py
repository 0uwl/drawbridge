from flask import Blueprint, request
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from drawbridge.db import get_session
from drawbridge.models import utcnow_iso
from drawbridge.queries import get_user_by_username
from drawbridge.utils import error_response, success_response


# NOTE: URL prefixes are defined and appended to the following routes when this blueprint is registered in main.py.
#       They should not be defined here

def create_blueprint():
    bp = Blueprint(name='auth', import_name= __name__)

    @bp.post('/login')
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

        login_user(user)
        return success_response('Logged in', payload=_user_payload(user))

    @bp.post('/reset-password')
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
            return error_response('Invalid reset request', 'invalid_reset', code=400, silent=True)

        user.password_hash = generate_password_hash(new_password)
        user.must_reset_password = False
        user.last_login_at = utcnow_iso()
        session.commit()

        login_user(user)
        return success_response('Password reset', payload=_user_payload(user))

    @bp.post('/claim')
    def claim():
        """First-time password creation for an admin-created local account.
        Unauthenticated by necessity (the account has no password yet), but
        only succeeds against a local account that has never been claimed —
        auth_source='local' and password_hash still NULL. SAML-only accounts
        also have a NULL password_hash; auth_source keeps the two from being
        conflated. First successful claim wins — see docs/authentication.md
        for why this race is an accepted tradeoff rather than a token flow."""
        data = request.get_json(silent=True) or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return error_response('Username and password are required', 'invalid_request', code=400)

        session = get_session()
        user = get_user_by_username(session, username)

        if not user or user.auth_source != 'local' or user.password_hash is not None:
            return error_response('Invalid claim request', 'invalid_claim', code=400, silent=True)

        user.password_hash = generate_password_hash(password)
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
            return error_response('Current password is incorrect', 'invalid_credentials', code=400, silent=True)

        session = get_session()
        current_user.password_hash = generate_password_hash(new_password)
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


def _user_payload(user):
    return {
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'auth_source': user.auth_source,
    }
