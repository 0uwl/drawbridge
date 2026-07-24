# Testing Approach

Tests live in `tests/` and use pytest. The Flask app must be testable without
a running Kea instance — the provisioning gate lives in
`drawbridge/api/leases.py`'s `/api/provision-request`/`/api/provision-complete`
routes, which have no Kea dependency at all (no hook, no Control Agent
calls), so no mocking of anything Kea-related is needed.

The SQLite database for tests uses a temporary file via a `tmp_path` fixture

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
- `GET /api/log` returns `ProvisioningLog` rows for any authenticated role (operator or admin); 401 when logged out
- `POST /api/auth/login` with correct/incorrect credentials → 200 / 401
- Accessing `/api/devices`, `/api/log`, or `/api/users` without a session → 401
- `POST /api/users` as a non-admin operator → 403
- Concurrent writes from multiple sessions (simulating multiple workers) don't raise unhandled `database is locked` errors

`drawbridge/db.py`'s PostgreSQL bootstrap-lock path (`_postgres_lock`) has
its own tier at `tests/postgres-integration/` — a plain `pytest` run
starts a throwaway Postgres container automatically (via a session-scoped
fixture in that directory's `conftest.py`) and tears it down after, so no
manual setup is needed. It skips cleanly if `podman` isn't installed,
rather than failing the run — see that directory's README.

`kea/*.conf` has static contract tests (`tests/test_kea_config.py`) that
run in the default suite — see `tests/kea-integration/README.md` for the
separate, opt-in, container-based tiers that require a real Kea process
and are not part of `pytest`.

`tests/browser-integration/` is a similar opt-in, non-`pytest` tier for
frontend behavior that only shows up in a real browser (e.g. whether a
failed login actually renders an error message) — see its README.

`scripts/ztp-base.py` assumes it only ever runs on a real IOS XE device
(`cli` is a hard import, no local-testing fallback). `tests/test_ztp_base.py`
still exercises its actual logic (trustpoint setup happens exactly once
per run, C9200CX vs. other-platform transport dispatch) by faking
`sys.modules['cli']` before loading it — same idea as Guestshell providing
a real one, no change needed to the script itself. Separately,
`tests/ztp_mock.py` is a `requests`-based simulation of the same
phone-home/completion/log-push contract with no `cli` involvement at all —
covered by `tests/test_ztp_mock.py`, and also runnable directly
(`python tests/ztp_mock.py --serial ...`) against a live `dev.sh`/Drawbridge
instance for manual smoke-testing.

Run with:
```bash
pytest -v
pytest tests/test_lease_api.py   # single file
pytest --tb=short                # brief tracebacks
```
