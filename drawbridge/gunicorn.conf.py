import os

from drawbridge.db import _is_sqlite
from drawbridge.main import DATABASE_PATH as _DEFAULT_DATABASE_PATH
from drawbridge.main import TLS_CERT_PATH as _DEFAULT_TLS_CERT_PATH
from drawbridge.main import TLS_KEY_PATH as _DEFAULT_TLS_KEY_PATH
from drawbridge.tls import ensure_cert

worker_class = 'gevent'

# SQLite is forced to a single worker — this is what makes the fcntl-based
# bootstrap lock in drawbridge/db.py unconditionally sufficient (only one
# process is ever inside it) and eliminates the multi-worker races
# documented in docs/kea-hook-findings.md #5. PostgreSQL has no such
# constraint; worker count is configurable via WORKERS.
if _is_sqlite(os.environ.get('DATABASE_PATH', _DEFAULT_DATABASE_PATH)):
    workers = 1
else:
    workers = int(os.environ.get('WORKERS', '4'))

timeout = 120
bind = f"0.0.0.0:{os.environ.get('DRAWBRIDGE_PORT', '8080')}"
accesslog = None
errorlog = '-'
capture_output = False

# Drawbridge terminates its own TLS by default (self-signed cert generated
# on first run if TLS_CERT_PATH/TLS_KEY_PATH aren't already there — see
# drawbridge/tls.py). TLS_DISABLED is a local-dev-only escape hatch —
# ponytail: dev-only, never set in production/Quadlet configs — for running
# a plain-HTTP Flask dev server without dealing with certs; the deployed
# container always has TLS on.
if not os.environ.get('TLS_DISABLED'):
    certfile = os.environ.get('TLS_CERT_PATH', _DEFAULT_TLS_CERT_PATH)
    keyfile = os.environ.get('TLS_KEY_PATH', _DEFAULT_TLS_KEY_PATH)
    ensure_cert(certfile, keyfile)
