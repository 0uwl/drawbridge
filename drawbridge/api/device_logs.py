import logging

from flask import Blueprint, request
from flask_login import login_required

from drawbridge.db import get_session
from drawbridge.device_events import detect_state
from drawbridge.models import PROVISIONING_STATES
from drawbridge.queries import (
    add_device_log_entry,
    find_active_session_by_ip,
    get_device,
    get_provisioning_session,
    list_device_logs,
    touch_session,
    update_session_state,
)
from drawbridge.utils import error_response, success_response


def create_blueprint():
    bp = Blueprint('device_logs', __name__)

    @bp.get('/device-logs')
    @login_required
    def get_device_logs():
        session = get_session()
        serial = request.args.get('serial')
        after_id = request.args.get('after_id', type=int)
        entries = list_device_logs(session, serial=serial, after_id=after_id)
        return success_response('Returned device logs', payload=[e.as_dict() for e in entries], level=logging.DEBUG)

    @bp.route('/device-logs', methods=['PUT', 'POST'])
    def post_device_log():
        """Two callers, two body shapes. The ZTP script's log_to_server()
        (see scripts/ztp_script.py) sends {serial, message} — source is
        stamped 'script', and the serial must resolve to a known Device or
        ProvisioningSession. rsyslog's omhttp action sends {ip, message} —
        source is stamped 'syslog', and the IP is best-effort correlated to
        an in-flight ProvisioningSession via find_active_session_by_ip; no
        match just means the row's serial stays null. Open route, no auth
        decorator — same posture as leases.py's provision-request/
        provision-complete: gated by lookup, not caller identity.

        PUT is accepted alongside POST for the same reason
        leases.py's /provision-complete does: on C9200CX, log_to_server()
        can only reach this via IOS XE's `copy` primitive (see
        docs/decisions.md, "C9200CX network stack isolation"), and `copy`
        to an HTTP(S) destination issues a PUT, not a POST. rsyslog's
        omhttp action (the other caller) still uses POST.

        Either shape may also include an explicit `state`: the script knows
        its own lifecycle steps (fetching a config, applying it) that no
        syslog line would ever announce, so rather than making it word
        messages to match device_events keywords, it can just declare the
        state directly and skip pattern matching entirely. Must be one of
        PROVISIONING_STATES — same set the frontend's STATE_BADGES maps to
        a badge class, so an invalid value can't end up silently rendered
        as the neutral fallback. Without an explicit state, the message is
        run through device_events.detect_state() as before. Either way, a
        resulting state is only applied when a serial was resolved."""
        # force=True: IOS XE `copy` sends PUT/POST without Content-Type: application/json
        data = request.get_json(force=True, silent=True) or {}
        serial = data.get('serial')
        ip = data.get('ip')
        message = data.get('message')
        explicit_state = data.get('state')
        if not message or (not serial and not ip):
            return error_response('Request body is missing required parameter serial/ip/message', 'missing_parameter', code=422)
        if explicit_state and explicit_state not in PROVISIONING_STATES:
            return error_response(f'{explicit_state} is not a valid state', 'invalid_state', code=422)

        session = get_session()
        if serial:
            if get_device(session, serial) is None and get_provisioning_session(session, serial) is None:
                return error_response(f'{serial} not found', 'device_not_found', code=404)
            source = 'script'
        else:
            source = 'syslog'
            matched = find_active_session_by_ip(session, ip)
            serial = matched.serial if matched is not None else None

        add_device_log_entry(session, serial=serial, source=source, message=message)

        if serial is not None:
            # Any log line at all is evidence the device is still alive —
            # not just ones that happen to match a state trigger below.
            touch_session(session, serial=serial)

        state = explicit_state or detect_state(message)
        if state is not None and serial is not None:
            update_session_state(session, serial=serial, state=state)

        session.commit()
        return success_response(f'{serial or ip} log recorded')

    return bp
