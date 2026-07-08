# Testing Approach

Tests live in `tests/` and use pytest. The Flask app must be testable without
a running Kea instance — the provisioning gate lives in
`drawbridge/api/leases.py`'s `/api/provision-request`/`/api/provision-complete`
routes, which have no Kea dependency at all (no hook, no Control Agent
calls), so no mocking of anything Kea-related is needed.

The SQLite database for tests uses a temporary file via a `tmp_path` fixture,
never the production `/srv/drawbridge/data/drawbridge.db`.

Key test cases to cover:
- `GET /api/provision-request` with known serial → 200
- `GET /api/provision-request` with unknown serial → 404
- `GET /api/provision-request` with missing serial → 422
- `POST /api/devices` idempotency (re-registering same serial)
- `ProvisioningLog` row written correctly on each provisioning decision
- `POST /api/provision-complete` deletes the `ProvisioningSession` row (not
  the `devices` row) and writes a `ProvisioningLog` row with image/config_file set
- `ProvisioningLog` rows older than the retention setting are purged on next insert; rows are kept when retention is `indefinite`
- `PUT /api/settings/log-retention` as admin updates the setting; as non-admin → 403
- `POST /api/auth/login` with correct/incorrect credentials → 200 / 401
- Accessing `/api/devices`, `/api/log`, or `/api/users` without a session → 401
- `POST /api/users` as a non-admin operator → 403
- Concurrent writes from multiple sessions (simulating multiple workers) don't raise unhandled `database is locked` errors

**Coverage note:** `drawbridge/db.py`'s PostgreSQL bootstrap-lock path
(`_postgres_lock`) is structurally identical to the tested SQLite path
(`_sqlite_lock`) but isn't itself exercised by the suite — no live Postgres
server is available in CI. This is an accepted alpha-scope gap, not
silently ignored.

Run with:
```bash
pytest -v
pytest tests/test_lease_api.py   # single file
pytest --tb=short                # brief tracebacks
```
