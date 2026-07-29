from flask import Blueprint, request

from drawbridge.db import get_session
from drawbridge.queries import (
    add_log_entry,
    create_provisioning_session,
    delete_provisioning_session,
    get_device_or_wildcard,
    get_file_by_version,
    get_provisioning_session,
    update_session_facts,
)
from drawbridge.utils import error_response, success_response

def create_blueprint():
    bp = Blueprint('leases', __name__)

    @bp.get('/provision-request')
    def provision_request():
        """Called by the ZTP script's phone-home step on boot (see
        scripts/ztp_script.py) — a plain GET with query-string params, since
        IOS XE's `copy` primitive (the only network I/O available from
        Guestshell, see docs/decisions.md) can't attach a request body.
        Open route, no auth decorator — same posture as /provision-complete
        below: gated by the serial lookup itself, not by caller identity.

        version is the device's own current version (from `show version`,
        already known locally before this call) — compared against the
        matched Device row's desired version to decide whether an image
        filename is included in the response payload. See docs/api.md."""
        serial = request.args.get('serial')
        if not serial:
            return error_response('Request is missing required parameter serial', 'missing_parameter', code=422)

        version = request.args.get('version')
        if not version:
            return error_response('Request is missing required parameter version', 'missing_parameter', code=422)

        mac = request.args.get('mac')  # optional; pinned alongside ip on first call for this
        # serial, compared (not overwritten) on repeats — see create_provisioning_session

        session = get_session()
        # Exact serial match, falling back to the 'allow all' wildcard entry
        # (serial='*') if one exists — see docs/decisions.md, "Wildcard
        # allowlist entry".
        device = get_device_or_wildcard(session, serial)

        if device is None:
            return error_response(f'{serial} not found', 'device_not_found', code=404)

        image = None
        if device.version and device.version != version:
            image_file = get_file_by_version(session, 'image', device.version)
            if image_file is None:
                # Fail closed (docs/decisions.md): the allowlist entry names
                # a desired version with no image mapped to it (e.g. the
                # image was deleted after this Device row was created) —
                # don't silently proceed with config only.
                return error_response(
                    f"{serial}'s desired version '{device.version}' has no image mapped to it",
                    'image_missing_for_version',
                    code=409,
                )
            image = image_file.filename

        ps = create_provisioning_session(
            session,
            serial=serial,
            mac=mac,
            ip=request.remote_addr,
            image=image,
            config_file=device.config_file,
        )
        if ps is None:
            return error_response(
                f'{serial} is already pinned to a different mac/ip', 'session_mismatch', code=409, silent=True,
            )
        session.commit()

        # device.as_dict()'s 'serial' is overridden below for the wildcard
        # case, where device.serial is literally '*', not the caller's real
        # serial. 'image' is the resolved filename (or None), not the
        # Device row's desired-version field.
        payload = {**device.as_dict(), 'serial': serial, 'image': image}
        return success_response(f'{serial} approved', payload=payload)

    @bp.route('/provision-request/facts', methods=['PUT', 'POST'])
    def provision_request_facts():
        """Called by the ZTP script once it has an approved session (see
        scripts/ztp_script.py's report_device_facts()) to report device
        facts (model, version) the initial GET /provision-request can't
        carry - a JSON body, sent the same way as /provision-complete: PUT
        via IOS XE's `copy` primitive on C9200CX, direct PUT elsewhere. Open
        route, same posture as /provision-request and /provision-complete -
        gated by the active session + IP match, not caller identity."""
        # force=True: IOS XE `copy` sends PUT without Content-Type: application/json
        data = request.get_json(force=True, silent=True)
        if data is None:
            return error_response('Empty request body', 'empty_request_body', code=422)

        serial = data.get('serial')
        if serial is None:
            return error_response('Request body is missing required parameter serial', 'missing_parameter', code=422)

        session = get_session()
        active = get_provisioning_session(session, serial)

        if active is None:
            return error_response(f'{serial} is not in active provisioning', 'device_not_active', code=404)

        if active.ip != request.remote_addr:
            return error_response(
                f'{serial} request does not match its active session', 'session_mismatch', code=409, silent=True,
            )

        update_session_facts(session, serial=serial, model=data.get('model'), version=data.get('version'))
        session.commit()

        return success_response(f'{serial} facts recorded')

    @bp.route('/provision-complete', methods=['PUT', 'POST'])
    def provision_complete():
        # force=True: IOS XE `copy` sends PUT without Content-Type: application/json
        data = request.get_json(force=True, silent=True)
        if data is None:
            return error_response('Empty request body', 'empty_request_body', code=422)

        serial = data.get('serial')
        if serial is None:
            return error_response('Request body is missing required parameter serial', 'missing_parameter', code=422)

        event = data.get('event', 'provision_complete')
        detail = data.get('detail')

        session = get_session()
        active = get_provisioning_session(session, serial)

        if active is None:
            return error_response(f'{serial} is not in active provisioning', 'device_not_active', code=404)

        if active.ip != request.remote_addr:
            return error_response(
                f'{serial} request does not match its active session', 'session_mismatch', code=409, silent=True,
            )

        # Falls back to the session's assigned image/config_file (set at
        # approval time from the Device row — see provision_request above)
        # when the device doesn't explicitly report its own. The alpha
        # scripts/ztp_script.py stub never does, so without this the log
        # would show blank image/config for every real completion despite
        # the assignment being known.
        image = data.get('image')
        if image is None:
            image = active.image

        config_file = data.get('config_file')
        if config_file is None:
            config_file = active.config_file

        add_log_entry(
            session,
            serial=serial,
            event=event,
            ip=active.ip,
            image=image,
            config_file=config_file,
            detail=detail,
        )
        delete_provisioning_session(session, serial)
        # DeviceLogEntry rows are deliberately NOT cleared here on a clean
        # success — an operator wants to see the raw run that just
        # completed, not just the "Provisioned" summary (see
        # frontend/src/views/Devices.vue). They're cleared instead when the
        # device is removed from the allowlist entirely (devices.py's
        # DELETE route) or age out via log_retention_days like everything
        # else — see docs/database.md, "Log Retention & Data Minimisation".
        session.commit()

        return success_response(f'{serial} provisioning recorded')

    return bp
