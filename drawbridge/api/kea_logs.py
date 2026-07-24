import logging

from flask import Blueprint, request
from flask_login import login_required

from drawbridge.db import get_session
from drawbridge.queries import add_kea_log_entry, list_kea_logs
from drawbridge.utils import error_response, success_response


def create_blueprint():
    bp = Blueprint('kea_logs', __name__)

    @bp.get('/kea-logs')
    @login_required
    def get_kea_logs():
        session = get_session()
        after_id = request.args.get('after_id', type=int)
        entries = list_kea_logs(session, after_id=after_id)
        return success_response('Returned Kea logs', payload=[e.as_dict() for e in entries], level=logging.DEBUG)

    @bp.post('/kea-logs')
    def post_kea_log():
        """Called by drawbridge-rsyslog's omhttp action (kea/rsyslog-kea-forward.conf
        forwards Kea's local0 syslog output here — see docs/logging.md).
        Open route, no auth decorator — same posture as POST /device-logs:
        the forwarder has no operator session to authenticate with, and
        there's no secret it could hold anyway."""
        data = request.get_json(force=True, silent=True) or {}
        message = data.get('message')
        if not message:
            return error_response('Request body is missing required parameter message', 'missing_parameter', code=422)

        session = get_session()
        add_kea_log_entry(session, message=message)
        session.commit()
        return success_response('Kea log recorded', silent=True)

    return bp
