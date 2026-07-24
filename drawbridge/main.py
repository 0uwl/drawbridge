import logging
import os
import secrets
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from drawbridge.auth import init_login_manager, limiter
from drawbridge.db import init_db
from drawbridge.utils import error_response

API_VERSION=1
API_PREFIX=f'/api/v{API_VERSION}'
DATABASE_PATH = '/app/data/drawbridge.db'
FILES_PATH = '/app/files'
LOG_LEVEL = 'INFO'
SQLITE_BUSY_TIMEOUT_MS = '1000'
LOG_RETENTION_DAYS = '30'
DEFAULT_IMAGE = None
DEFAULT_CONFIG_FILE = None
ADMIN_PASSWORD = None
CREDENTIALS_DIRECTORY = None
TLS_CERT_PATH = '/app/data/tls/cert.pem'
TLS_KEY_PATH = '/app/data/tls/key.pem'
SAML_SETTINGS_PATH = '/app/data/saml'

# Built Vue SPA (frontend/, baked in at image build time — see
# docs/frontend.md). static_folder is disabled below so Flask doesn't
# register its own implicit static route on the same URL pattern as the
# catch-all; this path is used directly instead.
FRONTEND_DIST = Path(__file__).resolve().parent / 'static'


def create_app(config_dict: dict = {}):
    """Flask app factory
    """

    app = Flask(__name__, static_folder=None)

    # Configuration (env vars per docs/deployment.md, overridable via config_dict for tests)
    app.config['DATABASE_PATH'] = os.getenv('DATABASE_PATH', DATABASE_PATH)
    app.config['FILES_PATH'] = os.getenv('FILES_PATH', FILES_PATH)
    app.config['LOG_LEVEL'] = os.getenv('LOG_LEVEL', LOG_LEVEL)
    app.config['TESTING'] = os.getenv('TESTING', False)
    app.config['SQLITE_BUSY_TIMEOUT_MS'] = int(os.getenv('SQLITE_BUSY_TIMEOUT_MS', SQLITE_BUSY_TIMEOUT_MS))
    app.config['LOG_RETENTION_DAYS'] = os.getenv('LOG_RETENTION_DAYS', LOG_RETENTION_DAYS)
    app.config['DEFAULT_IMAGE'] = os.getenv('DEFAULT_IMAGE', DEFAULT_IMAGE)
    app.config['DEFAULT_CONFIG_FILE'] = os.getenv('DEFAULT_CONFIG_FILE', DEFAULT_CONFIG_FILE)
    app.config['ADMIN_PASSWORD'] = os.getenv('ADMIN_PASSWORD', ADMIN_PASSWORD)
    app.config['CREDENTIALS_DIRECTORY'] = os.getenv('CREDENTIALS_DIRECTORY', CREDENTIALS_DIRECTORY)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')  # no default — see check below
    app.config['TLS_DISABLED'] = bool(os.getenv('TLS_DISABLED'))
    app.config['SAML_SETTINGS_PATH'] = os.getenv('SAML_SETTINGS_PATH', SAML_SETTINGS_PATH)

    if config_dict:
        app.config.update(config_dict)

    if app.testing:
        app.config['LOG_LEVEL'] = 'DEBUG'

    # Beta section 3 makes Drawbridge terminate its own TLS by default (see
    # drawbridge/gunicorn.conf.py), so there's no longer an "unencrypted
    # Drawbridge" deployment mode to gate against — except TLS_DISABLED,
    # the local-dev escape hatch, where SESSION_COOKIE_SECURE would silently
    # drop the session cookie over plain HTTP instead.
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = not app.testing and not app.config['TLS_DISABLED']

    # Off by default under testing so existing tests don't hit 429s; a
    # rate-limiting test can override this explicitly via config_dict.
    app.config.setdefault('RATELIMIT_ENABLED', not app.testing)

    if app.config['SECRET_KEY'] is None:
        if app.testing:
            # Ephemeral, this-process-only key. Fine for tests (one app
            # instance, no other worker needs to agree on it). Production
            # must set SECRET_KEY explicitly: each Gunicorn worker calls
            # create_app() independently post-fork (see db.py), so without
            # a shared env var every worker would sign session cookies with
            # a different key and sessions would randomly invalidate
            # depending on which worker handles the next request.
            app.config['SECRET_KEY'] = secrets.token_hex(32)
        else:
            raise RuntimeError(
                'SECRET_KEY environment variable must be set — see docs/deployment.md'
            )

    # Logger — gunicorn owns the handlers in production
    gunicorn_logger = logging.getLogger('gunicorn.error')
    gunicorn_logger.setLevel(app.config['LOG_LEVEL'])
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

    @app.route('/health')
    def health_check():
        return jsonify({'status': 'healthy'}), 200

    from drawbridge.api import auth
    from drawbridge.saml import SamlAuthBackend
    app.register_blueprint(auth.create_blueprint(), url_prefix=f'{API_PREFIX}/auth')
    app.logger.debug("Registered Blueprint 'auth.py'")

    saml_backend = SamlAuthBackend(app.config['SAML_SETTINGS_PATH'])
    app.register_blueprint(auth.create_saml_blueprint(saml_backend), url_prefix='/saml')
    app.logger.debug("Registered Blueprint 'saml'")

    from drawbridge.api import devices
    app.register_blueprint(devices.create_blueprint(), url_prefix=f'{API_PREFIX}/devices')
    app.logger.debug("Registered Blueprint 'devices.py'")

    from drawbridge.api import leases
    app.register_blueprint(leases.create_blueprint(), url_prefix=f'{API_PREFIX}')
    app.logger.debug("Registered Blueprint 'leases.py'")

    from drawbridge.api import files
    app.register_blueprint(files.create_blueprint(), url_prefix=f'/files')
    app.logger.debug("Registered Blueprint 'files.py'")

    from drawbridge.api import settings
    app.register_blueprint(settings.create_blueprint(), url_prefix=API_PREFIX)
    app.logger.debug("Registered Blueprint 'settings.py'")

    from drawbridge.api import device_logs
    app.register_blueprint(device_logs.create_blueprint(), url_prefix=API_PREFIX)
    app.logger.debug("Registered Blueprint 'device_logs.py'")

    from drawbridge.api import kea_logs
    app.register_blueprint(kea_logs.create_blueprint(), url_prefix=API_PREFIX)
    app.logger.debug("Registered Blueprint 'kea_logs.py'")

    # 'scripts' is deliberately not one of these — files.py no longer
    # manages a 'script' file type at all (see docs/decisions.md), so
    # files/scripts/ is install.sh's concern now, not this app's: it
    # creates and seeds that directory directly for drawbridge-bootstrap's
    # bind mount, entirely outside FILES_PATH's Flask-managed subtree.
    for subdir in ('images', 'configs'):
        os.makedirs(os.path.join(app.config['FILES_PATH'], subdir), exist_ok=True)

    init_login_manager(app)
    limiter.init_app(app)

    @app.errorhandler(429)
    def _rate_limited(e):
        return error_response('Too many requests', 'rate_limited', code=429)

    with app.app_context():
        init_db(app)

    # Serve the built Vue SPA. Registered last, but route order doesn't
    # matter here — Werkzeug matches literal/blueprint routes like
    # /api/provision-request ahead of this catch-all regardless of
    # registration order. Falls back to index.html for any unrecognised
    # path so Vue
    # Router's client-side routes resolve on a hard refresh (see
    # docs/frontend.md).
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_frontend(path):
        target = FRONTEND_DIST / path
        if path and target.is_file():
            return send_from_directory(FRONTEND_DIST, path)
        return send_from_directory(FRONTEND_DIST, 'index.html')

    app.logger.info(f"Finished creating Drawbridge app instance with log level {app.config['LOG_LEVEL']}")

    return app
