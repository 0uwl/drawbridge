import hashlib
import logging
import re

from flask import current_app, jsonify, request
from flask_login import current_user

# Below DEBUG (10) — success_response() fires on every read (device list,
# session list, etc.), not just meaningful writes, so it's opt-in noise
# rather than default-on INFO. Registered here (not main.py) because main.py
# imports this module at the top of the file, ahead of its own
# gunicorn_logger.setLevel(LOG_LEVEL) call — see main.py's LOG_LEVEL comment.
TRACE = 5
logging.addLevelName(TRACE, 'TRACE')


def success_response(msg, payload=None, code=200, silent=False, level=logging.INFO):
    """Standard success envelope: {success, message, payload}."""
    username = current_user.username if current_user.is_authenticated else 'anonymous'
    current_app.logger.log(TRACE, f'{msg} (user={username} ip={request.remote_addr})')
    if not silent:
        current_app.logger.log(level, msg)
    return jsonify({
        'success': True,
        'message': msg,
        'payload': payload
    }), code


def error_response(msg, error, code=400, silent=False, level=logging.ERROR):
    """Standard error envelope: {success, message, error}."""
    username = current_user.username if current_user.is_authenticated else 'anonymous'
    current_app.logger.log(TRACE, f'{msg} (user={username} ip={request.remote_addr})')
    if not silent:
        current_app.logger.log(level, msg)
    return jsonify({
        'success': False,
        'message': msg,
        'error': error
    }), code


def allowed_file(filename: str, allowed_extensions: set[str]) -> bool:
    """Check whether filename has one of the allowed extensions."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions


def hash_file(filepath: str) -> str:
    """Compute the SHA-256 hash of a file on disk, read in chunks.
    """
    algorithm = hashlib.sha256()

    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            algorithm.update(chunk)

    return algorithm.hexdigest()


_SHA256_RE = re.compile(r'^[0-9a-fA-F]{64}$')


def is_valid_sha256(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(value))
