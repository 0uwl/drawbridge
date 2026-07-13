import hashlib
import os
import tempfile

from flask import Blueprint, current_app, request, send_from_directory
from flask_login import current_user
from werkzeug.utils import secure_filename

from drawbridge.db import get_session
from drawbridge.queries import add_file, delete_file, get_file, list_files
from drawbridge.utils import allowed_file, error_response, success_response

CHUNK_SIZE = 64 * 1024

ALLOWED_EXTENSIONS = {
    'image':  {'bin', 'spa', 'pkg', 'tar'},
    'config': {'cfg', 'conf', 'txt'},
    'script': {'py', 'tcl', 'sh'},
}

_TYPE_SUBDIR = {
    'image':  'images',
    'config': 'configs',
    'script': 'scripts',
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
    return success_response(f'All {file_type}s', payload=[f.as_dict() for f in files])


def _handle_serve(file_type: str, filename: str):
    """Unauthenticated — devices fetch files over HTTP during ZTP."""
    db_session = get_session()
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

    add_file(
        db_session,
        file_type=file_type,
        filename=safe_name,
        size_bytes=size,
        sha256=digest.hexdigest(),
        uploaded_by=current_user.username,
    )
    db_session.commit()
    return success_response(f'{safe_name} uploaded', code=201)


def _handle_delete(file_type: str, filename: str):
    db_session = get_session()
    if not delete_file(db_session, file_type, filename):
        return error_response(f'{filename} not found', 'file_not_found', code=404)

    db_session.commit()

    file_path = os.path.join(_type_dir(file_type), filename)
    try:
        os.unlink(file_path)
    except OSError:
        current_app.logger.warning(f'DB row deleted for {filename} but disk file missing at {file_path}')

    return success_response(f'{filename} deleted')


def create_blueprint():
    bp = Blueprint('ztp', __name__)

    @bp.route('/images', defaults={'filename': None}, methods=['GET', 'POST'])
    @bp.route('/images/<path:filename>', methods=['GET', 'DELETE'])
    def images(filename=None):
        if request.method != 'GET' or filename is None:
            if err := _require_auth():
                return err
        match request.method:
            case 'GET':
                return _handle_serve('image', filename) if filename else _handle_list('image')
            case 'POST':
                return _handle_upload('image')
            case 'DELETE':
                assert filename is not None  # DELETE route always binds <filename>
                return _handle_delete('image', filename)
            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    @bp.route('/configs', defaults={'filename': None}, methods=['GET', 'POST'])
    @bp.route('/configs/<path:filename>', methods=['GET', 'DELETE'])
    def config_files(filename=None):
        if request.method != 'GET' or filename is None:
            if err := _require_auth():
                return err
        match request.method:
            case 'GET':
                return _handle_serve('config', filename) if filename else _handle_list('config')
            case 'POST':
                return _handle_upload('config')
            case 'DELETE':
                assert filename is not None  # DELETE route always binds <filename>
                return _handle_delete('config', filename)
            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    @bp.route('/scripts', defaults={'filename': None}, methods=['GET', 'POST'])
    @bp.route('/scripts/<path:filename>', methods=['GET', 'DELETE'])
    def scripts(filename=None):
        if request.method != 'GET' or filename is None:
            if err := _require_auth():
                return err
        match request.method:
            case 'GET':
                return _handle_serve('script', filename) if filename else _handle_list('script')
            case 'POST':
                return _handle_upload('script')
            case 'DELETE':
                assert filename is not None  # DELETE route always binds <filename>
                return _handle_delete('script', filename)
            case _:
                return error_response('Method not allowed', 'method_not_allowed', code=405, silent=True)

    return bp
