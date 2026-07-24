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
accesslog = None
errorlog = '-'
capture_output = False

# TLS termination is drawbridge-nginx's job now (see v0-3-2.md — a plain
# HTTP request against a socket Gunicorn itself wraps in TLS gets nothing
# but a connection reset; there's no way to redirect on a socket already
# committed to a handshake). Gunicorn's own bind is an internal-only,
# plain-HTTP port nginx proxies to — 127.0.0.1 so it's reachable only from
# other containers sharing the pod's network namespace, never published
# directly. Fixed, not DRAWBRIDGE_PORT-derived: nothing outside the pod
# ever needs to know or reach it, same posture as Kea Control Agent's
# 127.0.0.1:8081 (see docs/decisions.md, "hardcoded 8080s").
#
# TLS_DISABLED is the exception: a supported "bring your own reverse
# proxy" mode (not just local dev anymore — see docs/deployment.md, "TLS")
# for an operator who doesn't want drawbridge-nginx at all. In that mode
# Gunicorn binds DRAWBRIDGE_PORT directly, in plain HTTP, exactly as it
# always has — the operator's own proxy (or nothing, for local dev) is
# expected to reach it there instead.
if os.environ.get('TLS_DISABLED'):
    bind = f"0.0.0.0:{os.environ.get('DRAWBRIDGE_PORT', '8080')}"
else:
    bind = '127.0.0.1:8078'
    # Deliberately NOT named certfile/keyfile: those are real Gunicorn
    # settings, and this file is executed as Gunicorn's own config module
    # — module-level names matching a setting are picked up automatically.
    # Naming these anything else avoids silently re-wrapping Gunicorn's
    # own (now-internal-only) socket in TLS a second time. Still need to
    # call ensure_cert() here even though Gunicorn's own bind is no longer
    # TLS — drawbridge-nginx reads this same cert/key (bind-mounted
    # read-only, see quadlet/drawbridge-nginx.container) to do the actual
    # termination; ensure_cert() is idempotent/first-run-only either way.
    _tls_cert_path = os.environ.get('TLS_CERT_PATH', _DEFAULT_TLS_CERT_PATH)
    _tls_key_path = os.environ.get('TLS_KEY_PATH', _DEFAULT_TLS_KEY_PATH)
    ensure_cert(_tls_cert_path, _tls_key_path)
