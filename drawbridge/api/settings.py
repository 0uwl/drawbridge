import logging

from flask import Blueprint, request
from flask_login import current_user, login_required

from drawbridge.auth import admin_required
from drawbridge.db import get_session
from drawbridge.queries import (
    clear_user_password,
    count_admins,
    create_user,
    delete_user,
    get_setting,
    get_user_by_id,
    list_provisioning_log,
    list_users,
    set_setting,
)
from drawbridge.utils import error_response, success_response

VALID_ROLES = ('admin', 'operator')


# NOTE: URL prefixes are defined and appended to the following routes when this blueprint is registered in main.py.
#       They should not be defined here

def create_blueprint():
    bp = Blueprint(name='settings', import_name=__name__)

    @bp.route('/users', methods=['GET', 'POST'])
    @admin_required
    def users():
        match (request.method):
            case 'GET':
                session = get_session()
                return success_response('Returned all users', payload=[_user_dict(u) for u in list_users(session)], level=logging.DEBUG)
            case 'POST':
                data = request.get_json(silent=True) or {}
                username = (data.get('username') or '').strip()
                role = data.get('role')

                if not username or role not in VALID_ROLES:
                    return error_response(
                        f"username is required and role must be one of {VALID_ROLES}",
                        'invalid_request',
                        code=422,
                    )

                session = get_session()
                user = create_user(session, username=username, role=role)
                session.commit()
                return success_response(
                    f'{username} created', payload={**_user_dict(user), 'claim_token': user.claim_token},
                )
            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    @bp.route('/users/<int:user_id>', methods=['PUT', 'DELETE'])
    @admin_required
    def user_actions(user_id: int):
        session = get_session()
        user = get_user_by_id(session, user_id)
        if user is None:
            return error_response(f"User with ID {user_id} not found", 'user_not_found', code=404)

        match (request.method):
            case 'PUT':
                data = request.get_json(silent=True) or {}
                role = data.get('role')
                if role not in VALID_ROLES:
                    return error_response(f'role must be one of {VALID_ROLES}', 'invalid_request', code=422)

                if user.role == 'admin' and role != 'admin' and count_admins(session) <= 1:
                    return error_response(
                        'Cannot demote the last remaining admin',
                        'last_admin',
                        code=409,
                    )

                user.role = role
                session.commit()
                return success_response(f'{user.username} updated', payload=_user_dict(user))

            case 'DELETE':
                if user.role == 'admin' and count_admins(session) <= 1:
                    return error_response(
                        'Cannot delete the last remaining admin',
                        'last_admin',
                        code=409,
                    )

                delete_user(session, user_id)
                session.commit()
                return success_response(f'{user.username} deleted')

            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    @bp.post('/users/<int:user_id>/reset-password')
    @admin_required
    def admin_reset_password(user_id: int):
        """Lets an admin force a claimed local account to re-claim a new
        password, without the delete-and-recreate workaround alpha required.
        Reuses the claim-token mechanism (POST /auth/claim), not
        /auth/reset-password - see docs/authentication.md."""
        session = get_session()
        user = get_user_by_id(session, user_id)
        if user is None:
            return error_response(f"User with ID {user_id} not found", 'user_not_found', code=404)

        if user.auth_source != 'local':
            return error_response('Only local accounts can be reset this way', 'invalid_request', code=400)

        clear_user_password(session, user)
        session.commit()
        return success_response(
            f"User '{user.username}' must claim a new password",
            payload={**_user_dict(user), 'claim_token': user.claim_token},
        )

    @bp.get('/log')
    @login_required
    def get_log():
        session = get_session()
        entries = list_provisioning_log(session)
        return success_response('Returned provisioning log', payload=[e.as_dict() for e in entries], level=logging.DEBUG)

    @bp.get('/settings/log-retention')
    @login_required
    def get_log_retention():
        session = get_session()
        setting = get_setting(session, 'log_retention_days')
        return success_response('Returned log retention setting', payload={'log_retention_days': setting.value if setting else None}, level=logging.DEBUG)

    @bp.put('/settings/log-retention')
    @admin_required
    def put_log_retention():
        data = request.get_json(silent=True) or {}
        value = data.get('log_retention_days')

        if value != 'indefinite':
            try:
                if int(value) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return error_response("log_retention_days must be a non-negative integer or 'indefinite'", 'invalid_request', code=422)
            value = str(value)

        session = get_session()
        set_setting(session, 'log_retention_days', value, updated_by=current_user.username)
        session.commit()
        return success_response('Log retention updated', payload={'log_retention_days': value})

    return bp


def _user_dict(user) -> dict:
    return {
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'auth_source': user.auth_source,
        'is_claimed': user.password_hash is not None,
        'created_at': user.created_at,
        'last_login_at': user.last_login_at,
    }
