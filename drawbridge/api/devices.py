import logging

from flask import Blueprint, request
from flask_login import current_user, login_required

from drawbridge.db import get_session
from drawbridge.models import SESSION_STALE_AFTER_MINUTES
from drawbridge.queries import (
    add_device,
    add_log_entry,
    delete_device,
    delete_device_logs_by_serial,
    delete_provisioning_session,
    get_device,
    get_provisioning_session,
    list_devices,
    list_sessions,
)
from drawbridge.utils import error_response, success_response


# NOTE: URL prefixes are defined and appended to the following routes when this blueprint is registered in main.py.
#       They should not be defined here

def create_blueprint():
    bp = Blueprint(name='devices', import_name= __name__)

    @bp.route('/', methods=['GET', 'POST'])
    @login_required
    def devices():
        match (request.method):
            case 'GET':
                session = get_session()
                devices = list_devices(session)
                return success_response('Returned all devices', payload=[d.as_dict() for d in devices], level=logging.DEBUG)
            case 'POST':
                data = request.get_json(silent=True) or {}
                serial = data.get('serial')
                if serial is None:
                    return error_response('Request body is missing required parameter serial', 'missing_parameter', code=422)
                session = get_session()
                add_device(
                    session,
                    serial=serial,
                    mac=data.get('mac'),
                    description=data.get('description'),
                    image=data.get('image'),
                    config_file=data.get('config_file'),
                    script=data.get('script'),
                    added_by=current_user.username,
                )
                session.commit()
                return success_response(f'{serial} added')
            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)


    @bp.route('/<string:serial>', methods=['GET', 'DELETE'])
    @login_required
    def device_actions(serial: str):
        session = get_session()
        device = get_device(session, serial)
        if device is None:
            return error_response(f'{serial} not found', 'device_not_found', code=404)

        match (request.method):
            case 'GET':
                return success_response(f'{serial} delivered', payload=device.as_dict(), level=logging.DEBUG)

            case 'DELETE':
                if get_provisioning_session(session, serial) is not None:
                    return error_response(
                        f'{serial} has an active provisioning session and cannot be deleted',
                        'active_session_exists',
                        code=409,
                    )
                delete_device(session, serial)
                # A device that's no longer allowlisted has nothing left for
                # Drawbridge to track except its ProvisioningLog history —
                # see docs/database.md, "Log Retention & Data Minimisation".
                delete_device_logs_by_serial(session, serial)
                session.commit()
                return success_response(f'{serial} deleted')

            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    @bp.get('/sessions')
    @login_required
    def list_active_sessions():
        db_session = get_session()
        active_sessions = list_sessions(db_session)
        return success_response('Returned active sessions', payload=[s.as_dict() for s in active_sessions], level=logging.DEBUG)

    @bp.route('/sessions/<string:serial>', methods=['GET', 'DELETE'])
    @login_required
    def session_actions(serial: str):
        db_session = get_session()
        active = get_provisioning_session(db_session, serial)
        if active is None:
            return error_response(f'Session for {serial} not found', 'session_not_found', code=404)

        match (request.method):
            case 'GET':
                return success_response(f'Session for {serial}', payload=active.as_dict(), level=logging.DEBUG)

            case 'DELETE':
                # See docs/database.md, "Stale sessions" — cancellation is
                # only ever admin-initiated, and only once the session has
                # been quiet longer than the timeout. Never auto-expired.
                if not active.is_stale():
                    return error_response(
                        f"{serial}'s session is still active and cannot be cancelled yet",
                        'session_not_stale',
                        code=409,
                    )
                add_log_entry(
                    db_session,
                    serial=serial,
                    event='provision_cancelled',
                    ip=active.ip,
                    image=active.image,
                    config_file=active.config_file,
                    detail=f'Cancelled by operator after {SESSION_STALE_AFTER_MINUTES} minutes of inactivity',
                )
                delete_provisioning_session(db_session, serial)
                db_session.commit()
                return success_response(f'{serial} session cancelled')

            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    return bp