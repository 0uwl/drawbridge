import os

from drawbridge.db import _is_sqlite
from drawbridge.main import DATABASE_PATH as _DEFAULT_DATABASE_PATH

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
