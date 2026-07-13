# PostgreSQL integration tests

`tests/test_db.py` exercises `drawbridge/db.py`'s SQLite bootstrap path
(`_sqlite_lock`, the SQLite branch of `_is_first_run`) against a real
temp-file database on every default `pytest` run. The PostgreSQL branch of
that same code (`_postgres_lock`, `pg_advisory_lock`/`unlock`) is
structurally parallel but needs an actual Postgres server to exercise —
this directory is that tier.

## Running it

Nothing to do — `pytest`, run normally from the repo root, starts a
throwaway `postgres:16-alpine` container via `podman` before the first
test in this directory and stops it once the whole session finishes (see
the session-scoped `pg_url` fixture in `conftest.py`). A random free host
port is used, so it won't collide with anything already listening on 5432.

```bash
pytest                              # full suite, SQLite + this tier
pytest tests/postgres-integration/  # just this tier
```

If `podman` isn't installed, this tier's tests are collected but **skip**
cleanly (reason: "podman not found") rather than failing the run — the
rest of the suite is unaffected.

To point at a Postgres you already manage (e.g. a CI service container)
instead of spinning one up here, set `DRAWBRIDGE_TEST_POSTGRES_URL` before
running `pytest`; the fixture uses it as-is and skips container management
entirely.

Each test drops and recreates the `public` schema before running (the
`clean_pg` fixture in `test_postgres_backend.py`) so the one
session-scoped container is reused across the whole file without leftover
state between tests.
