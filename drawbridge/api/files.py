import hashlib
import logging
import os
import tempfile

from flask import Blueprint, current_app, request, send_from_directory
from flask_login import current_user
from werkzeug.utils import secure_filename

from drawbridge.db import get_session
from drawbridge.queries import add_file, delete_file, find_active_session_by_ip, get_file, list_files, update_file_hash
from drawbridge.utils import allowed_file, error_response, is_valid_sha256, success_response

CHUNK_SIZE = 64 * 1024

ALLOWED_EXTENSIONS = {
    'image':  {'bin', 'spa', 'pkg', 'tar'},
    'config': {'cfg', 'conf', 'txt'},
}

_TYPE_SUBDIR = {
    'image':  'images',
    'config': 'configs',
}


def _type_dir(file_type: str) -> str:
    return os.path.join(current_app.config['FILES_PATH'], _TYPE_SUBDIR[file_type])


def _require_auth():
    """Returns a 401 error response when the caller is not authenticated, else None."""
    if not current_user.is_authenticated:
        return error_response('Authentication required', 'unauthorized', code=401, silent=True)
    return None


def _handle_list(file_type: str):
    db_session = get_session()
    files = list_files(db_session, file_type)
    return success_response(f"Returned all {file_type}s", payload=[f.as_dict() for f in files], level=logging.DEBUG)


def _handle_serve(file_type: str, filename: str):
    """Unauthenticated — devices fetch files over HTTP during ZTP. Requires
    the caller's IP to match an active ProvisioningSession (beta.md §7). The
    ZTP script itself is not served this way — it's fetched via DHCP Option
    67 from the separate drawbridge-bootstrap container before any
    serial/session exists (see docs/decisions.md, "HTTPS cert trust on
    C9200CX")."""
    db_session = get_session()
    if find_active_session_by_ip(db_session, request.remote_addr) is None:
        return error_response(
            'No active provisioning session for this request', 'no_active_session', code=403, silent=True,
        )
    if get_file(db_session, file_type, filename) is None:
        return error_response(f'{filename} not found', 'file_not_found', code=404)
    return send_from_directory(_type_dir(file_type), filename)


def _handle_upload(file_type: str):
    if 'file' not in request.files:
        return error_response('No file part in request', 'missing_file', code=422)

    upload = request.files['file']
    if not upload.filename:
        return error_response('No filename provided', 'missing_filename', code=422)

    safe_name = secure_filename(upload.filename)
    if not safe_name:
        return error_response('Invalid filename', 'invalid_filename', code=422)

    if not allowed_file(safe_name, ALLOWED_EXTENSIONS[file_type]):
        allowed = ', '.join(sorted(ALLOWED_EXTENSIONS[file_type]))
        return error_response(
            f'File extension not allowed for {file_type}s. Allowed: {allowed}',
            'invalid_extension',
            code=422,
        )

    expected_sha256 = request.form.get('sha256', '').strip()
    if expected_sha256 and not is_valid_sha256(expected_sha256):
        return error_response('sha256 must be 64 hex characters', 'invalid_hash', code=422)

    db_session = get_session()
    if get_file(db_session, file_type, safe_name) is not None:
        return error_response(
            f'{safe_name} already exists — delete it first to replace it',
            'file_exists',
            code=409,
        )

    type_dir = _type_dir(file_type)
    digest = hashlib.sha256()
    size = 0

    tmp_fd, tmp_path = tempfile.mkstemp(dir=type_dir)
    try:
        with os.fdopen(tmp_fd, 'wb') as f:
            while chunk := upload.stream.read(CHUNK_SIZE):
                f.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        os.rename(tmp_path, os.path.join(type_dir, safe_name))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
        os.unlink(os.path.join(type_dir, safe_name))
        return error_response(
            'Uploaded file does not match the supplied sha256',
            'hash_mismatch',
            code=422,
        )

    add_file(
        db_session,
        file_type=file_type,
        filename=safe_name,
        size_bytes=size,
        sha256=digest.hexdigest(),
        uploaded_by=current_user.username,
    )
    db_session.commit()
    return success_response(f"File '{safe_name}' uploaded", code=201)


def _handle_update_hash(file_type: str, filename: str):
    data = request.get_json(silent=True) or {}
    sha256 = (data.get('sha256') or '').strip()
    if not is_valid_sha256(sha256):
        return error_response('sha256 must be 64 hex characters', 'invalid_hash', code=422)

    db_session = get_session()
    f = update_file_hash(db_session, file_type, filename, sha256)
    if f is None:
        return error_response(f"File '{filename}' not found", 'file_not_found', code=404)

    db_session.commit()
    return success_response(f"File '{filename}' updated", payload=f.as_dict())


def _handle_delete(file_type: str, filename: str):
    db_session = get_session()
    if not delete_file(db_session, file_type, filename):
        return error_response(f"File '{filename}' not found", 'file_not_found', code=404)

    db_session.commit()

    file_path = os.path.join(_type_dir(file_type), filename)
    try:
        os.unlink(file_path)
    except OSError:
        current_app.logger.warning(f"DB row deleted for file '{filename}' but disk file missing at {file_path}")

    return success_response(f"File '{filename}' deleted")


def create_blueprint():
    bp = Blueprint('ztp', __name__)

    @bp.route('/images', defaults={'filename': None}, methods=['GET', 'POST'])
    @bp.route('/images/<path:filename>', methods=['GET', 'PUT', 'DELETE'])
    def images(filename=None):
        if request.method != 'GET' or filename is None:
            if err := _require_auth():
                return err
        match request.method:
            case 'GET':
                return _handle_serve('image', filename) if filename else _handle_list('image')
            case 'POST':
                return _handle_upload('image')
            case 'PUT':
                assert filename is not None  # PUT route always binds <filename>
                return _handle_update_hash('image', filename)
            case 'DELETE':
                assert filename is not None  # DELETE route always binds <filename>
                return _handle_delete('image', filename)
            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    @bp.route('/configs', defaults={'filename': None}, methods=['GET', 'POST'])
    @bp.route('/configs/<path:filename>', methods=['GET', 'PUT', 'DELETE'])
    def config_files(filename=None):
        if request.method != 'GET' or filename is None:
            if err := _require_auth():
                return err
        match request.method:
            case 'GET':
                return _handle_serve('config', filename) if filename else _handle_list('config')
            case 'POST':
                return _handle_upload('config')
            case 'PUT':
                assert filename is not None  # PUT route always binds <filename>
                return _handle_update_hash('config', filename)
            case 'DELETE':
                assert filename is not None  # DELETE route always binds <filename>
                return _handle_delete('config', filename)
            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    return bp
