import logging

from flask import Blueprint, request
from flask_login import login_required

from drawbridge.db import get_session
from drawbridge.queries import add_device_log_entry, get_device, get_provisioning_session, list_device_logs
from drawbridge.utils import error_response, success_response


def create_blueprint():
    bp = Blueprint('device_logs', __name__)

    @bp.get('/device-logs')
    @login_required
    def get_device_logs():
        session = get_session()
        serial = request.args.get('serial')
        entries = list_device_logs(session, serial=serial)
        return success_response('Returned device logs', payload=[e.as_dict() for e in entries], level=logging.DEBUG)

    @bp.post('/device-logs')
    def post_device_log():
        """Called by the ZTP script's log_to_server() (see scripts/ztp-base.py).
        Open route, no auth decorator — same posture as leases.py's
        provision-request/provision-complete: gated by serial lookup, not
        caller identity. source is never client-supplied; the server always
        stamps 'script' here — 'syslog' rows are written directly by
        drawbridge/log_poller.py, which already holds a DB session."""
        # force=True: IOS XE `copy` sends PUT/POST without Content-Type: application/json
        data = request.get_json(force=True, silent=True) or {}
        serial = data.get('serial')
        message = data.get('message')
        if not serial or not message:
            return error_response('Request body is missing required parameter serial/message', 'missing_parameter', code=422)

        session = get_session()
        if get_device(session, serial) is None and get_provisioning_session(session, serial) is None:
            return error_response(f'{serial} not found', 'device_not_found', code=404)

        add_device_log_entry(session, serial=serial, source='script', message=message)
        session.commit()
        return success_response(f'{serial} log recorded')

    return bp
