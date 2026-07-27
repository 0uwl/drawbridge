**Part A and Part B are superseded.** The DHCP-level Kea hook gate
described here was abandoned in favor of a script-level gate — see
[decisions.md](decisions.md) ("Provisioning gate moved from DHCP-level to
script-level"). Kept for reference; Part A's native-hook design, the
`unpark()`/DROP bug, and Part B's `host_cmds`+Postgres requirement remain
accurate Kea behavior even though this plan is no longer being
implemented. **Part C (pluggable SQLite/PostgreSQL for Drawbridge's own
database) is unaffected and has been implemented** — see
[database.md](database.md).

# Step 6 revised: Kea hook fix + Kea's own PostgreSQL + Drawbridge single-worker

Implementation plan for alpha.md step 6, revised after the investigation in
[kea-hook-findings.md](kea-hook-findings.md). This is the plan to resume
from — the code it describes was reverted, this document was not.

## Context

Implementing alpha.md step 6 (the `leases4_committed` hook + a Kea test
harness) surfaced several real problems only visible once actually run
against real Kea 3.0.3 and real Gunicorn — all written up in
[kea-hook-findings.md](kea-hook-findings.md). Summary of what's confirmed
and what it means going forward:

1. **Hook must be a native compiled library**, not `run_script` (which is
   fire-and-forget and can't gate the packet at all).
2. **`ParkingLotHandle::unpark()` always resumes normal processing** (sends
   the DHCPACK) regardless of `CalloutHandle::setStatus()` — the actual
   "drop this packet, send nothing" primitive is `drop()`. Get this right
   from the start this time: approved → `unpark()`, denied → `drop()`.
3. **Serial/MAC allowlist fallback** — `drawbridge/queries.py`'s
   `get_device()` and `api/leases.py`'s `lease_event()` only matched by
   primary key `serial`, but IOS XE alternates DHCP Option 61 between serial
   and MAC across retries (`docs/architecture.md`'s DHCP Flow note). This
   was fixed in the reverted work (a `get_device_by_serial_or_mac()`
   fallback, plus fixing `create_provisioning_session()` to key on the
   resolved `device.serial` rather than the raw request field — otherwise
   `/api/provision-complete` 404s on a MAC-keyed session) — **redo this
   fix**, it's small, correct, and independent of everything else here.
   Covered by a new `tests/test_lease_api.py` (didn't exist before).
4. **`host_cmds`' `reservation-add`/`reservation-del` require a real
   PostgreSQL or MySQL hosts-database** — confirmed directly against real
   Kea: there is no memfile/SQLite equivalent for *dynamic* host storage
   (unlike leases, which do support memfile). This is a hard Kea
   requirement, independent of anything about Drawbridge, and needs a real
   Postgres server in both the test harness and any real deployment.
5. **A genuine Gunicorn multi-worker race in Drawbridge's own SQLite
   bootstrap** — reproduces almost every time under the real production
   entrypoint (4 forked workers racing on `CREATE TABLE`/seed rows/admin
   bootstrap against a fresh `DATABASE_PATH`). Ad hoc `except
   IntegrityError`/`except OperationalError` patches around individual
   statements are **not sufficient** — the race has more than one shape and
   was observed to still occasionally lose data silently. The real fix is a
   lock around the *entire* schema+seed+bootstrap sequence.

Decision on the last two: since Postgres is *already* unavoidable for Kea's
own reservation storage, the question became whether Drawbridge's own app
database should also move to it. Resolved as: **no, not for alpha** — the
race is fundamentally a multi-*process* problem (Gunicorn forking workers),
and Drawbridge's traffic (a handful of devices, a few admin users) was never
throughput-bound, so removing the extra worker processes fixes the root
cause more simply than adding a database server. Instead: **support both
SQLite and PostgreSQL as pluggable backends**, SQLite forced to a single
worker (which is what makes a simple bootstrap lock unconditionally
sufficient), Postgres allowed multiple workers (using a native advisory
lock for the same critical section instead of a file lock). SQLite stays
the default for alpha; Postgres is available without needing a later
rewrite. Kea's own hosts-database is unaffected by this choice — it's a
separate system with its own PostgreSQL instance either way.

## Part A: The hook itself

- `kea/hook/lease_event_hook.cc`: standard Kea hook entry points
  (`version()`, `load(LibraryHandle&)`, `unload()`,
  `multi_threading_compatible()` returning 1 — Kea 3.0 rejects hook
  libraries that don't declare this), registering a `leases4_committed`
  callout.
- At `load()`, read `url` (required — Drawbridge's `/api/lease-event`),
  `timeout_ms` (default 2000), optional `api_key` from the library's
  `parameters`.
- Callout: pull the client-id (Option 61 → `serial`, RFC 2132 format — first
  octet is a type tag, hex-encode the remainder if not printable ASCII) and
  hardware address (→ `mac`, via `query4->getHWAddr()->toText(false)`,
  always populated regardless of Option 61's content) off the query packet
  via `handle.getArgument("query4", query4)`; the committed lease's address
  (→ `ip`) via `handle.getArgument("leases4", leases4)`. Build the JSON body
  with Kea's own `isc::data::Element` (linked via `kea.pc`, no new JSON
  dependency). Reference the packet
  (`handle.getParkingLotHandlePtr()->reference(query4)`), set
  `NEXT_STEP_PARK`, and hand off to a detached `std::thread` capturing
  `query4` (a `Pkt4Ptr`/shared_ptr — this keeps both the packet and its
  associated `CalloutHandle` alive across the async boundary; `Pkt` inherits
  `hooks::CalloutHandleAssociate`, so `query4->getCalloutHandle()` is the
  same object as `handle`) that does the HTTP POST via libcurl
  (respecting `timeout_ms`) and then:
  - approved (HTTP 200) → `setStatus(NEXT_STEP_CONTINUE)` +
    `parking_lot->unpark(query4)`
  - denied (anything else) → `setStatus(NEXT_STEP_DROP)` +
    `parking_lot->drop(query4)` — **not** `unpark()`, see finding #2.
- `kea/hook/CMakeLists.txt`: `pkg_check_modules(KEA REQUIRED IMPORTED_TARGET
  kea)` + `find_package(CURL)` + `find_package(Threads)`, link
  `PkgConfig::KEA CURL::libcurl Threads::Threads`, `PREFIX ""` /
  `OUTPUT_NAME "lease_event_hook"` / `SUFFIX ".so"`.
- Build/link dependencies confirmed against real Kea 3.0.3 (Ubuntu 24.04,
  ISC's `kea-3-0` Cloudsmith apt repo): `isc-kea-dev`, `isc-kea-hooks`,
  plus — non-obviously — `isc-kea-mysql` and `isc-kea-pgsql` are needed at
  **link time** even though this hook doesn't use them, because `kea.pc`'s
  `Libs:` line unconditionally references `-lkea-mysql -lkea-pgsql`. Same
  packages needed again at runtime for the resulting `.so`'s dynamic
  linking to resolve.

## Part B: Kea's hosts-database (PostgreSQL) — required, not optional

- `kea/kea-dhcp4.conf`: keep `lease-database` as `memfile` (leases don't
  need a server). Add a `hosts-database` block:
  ```json
  "hosts-database": {
      "type": "postgresql",
      "host": "<postgres host>",
      "name": "kea",
      "user": "kea",
      "password": "<dev/test placeholder, same pattern as api_key below>"
  }
  ```
  and load `libdhcp_pgsql.so` in `hooks-libraries` **before**
  `libdhcp_host_cmds.so` (verified working order against real Kea 3.0.3 —
  without both the `hosts-database` block *and* `libdhcp_pgsql.so`
  explicitly loaded, Kea fails with `Unable to open database: ... has not
  been compiled with support for host database type: postgresql` — a
  misleading message; it's a missing hooks-library, not a compile-time
  issue).
- Fix the stale Option 67 path (`/scripts/ztp_script.py` doesn't match the
  real `/files/scripts/<filename>` route) and use plain `http://`, not
  `https://` (no TLS termination exists in front of Drawbridge yet).
- `tests/kea-integration/`: `Containerfile.kea` (multi-stage — builder
  compiles the hook against `isc-kea-dev`, runtime stage installs
  `isc-kea-dhcp4 isc-kea-ctrl-agent isc-kea-hooks isc-kea-mysql
  isc-kea-pgsql libcurl4`), `docker-compose.yml` (three-plus-one services on
  one `192.168.100.0/24` bridge network with an **explicit gateway**
  outside the static IPs/pool — Docker defaults the bridge gateway to `.1`,
  which collided with `kea`'s static address the first time): `drawbridge`,
  `kea`, a new `postgres` service (`postgres:16-alpine`, confirmed
  working), and `simulator` (scapy-based DHCP client, gated behind a
  compose `profiles: [tools]` so it's not part of `up` and is invoked
  on-demand via `docker compose run --rm simulator ...`).
- `entrypoint.sh` (in the `kea` container): run `kea-admin db-init pgsql`
  against the postgres service before starting `kea-dhcp4`, tolerating an
  "already initialized" failure so re-starting the container against a warm
  Postgres volume doesn't crash. Also patches `kea-ctrl-agent.conf`'s
  `http-host` from `127.0.0.1` to `0.0.0.0` **only in the running copy**
  (`sed` into `/tmp`, leaving the checked-in file untouched) — production's
  `127.0.0.1` binding is correct there (Kea and Drawbridge share the host's
  loopback via Podman's `AddHost`/host-gateway trick per `docs/kea.md`); in
  a container-per-service harness that's a different, unreachable loopback,
  not a security downgrade to replicate.
- `README.md`: how to run it, noting this harness doesn't exercise
  HTTPS/cert validation or real Guestshell behavior — DHCP/hook/allowlist
  contract only.
- `docs/kea.md`: document the `hosts-database` requirement, link to
  `kea-hook-findings.md`.
- `docs/decisions.md`: record why Postgres is required here (separate
  entry from the Drawbridge-DB decision below — this one is unconditional,
  not a choice).

## Part C: Drawbridge — pluggable SQLite/PostgreSQL, SQLite forced single-worker

- `drawbridge/db.py`:
  - Add a small `_is_sqlite(database_path)` helper (same `'://' in path`
    sniffing `_database_url()` already does) so `gunicorn.conf.py` can
    reuse it without duplicating logic.
  - Replace the per-statement `except IntegrityError`/`except
    OperationalError` approach with a single critical section
    (`_bootstrap_once`) wrapping schema creation + seeding + admin
    bootstrap: for SQLite, an `fcntl.flock`-based advisory lock on
    `DATABASE_PATH + '.bootstrap-lock'`; for Postgres, a
    `pg_advisory_lock`/`pg_advisory_unlock` (via `session.execute(text(...))`
    with a fixed lock key) around the same sequence — same structure, a
    different underlying primitive per dialect, not a fork in logic.
  - First-run detection: SQLite keeps the existing file-existence check
    (`not Path(database_path).exists()`, checked *before* `create_all()`).
    Postgres has no file to check — use
    `sqlalchemy.inspect(engine).has_table('users')` instead (dialect-
    agnostic, works checked before or after `create_all()` runs).
  - Verify with a real `multiprocessing`-based test (not threads — the
    race is between OS processes, and threads inside one process didn't
    reliably reproduce it) spawning several real processes barrier-
    synchronized against one fresh `DATABASE_PATH`, asserting exactly one
    admin row and one settings row survive, and that all processes exit 0.
- `drawbridge/gunicorn.conf.py`: compute `workers` at startup —
  `_is_sqlite(os.environ.get('DATABASE_PATH', ...))` → `workers = 1`
  unconditionally; otherwise `workers = int(os.environ.get('WORKERS', '4'))`.
  This is what actually makes "SQLite ⇒ single process" a real, enforced
  constraint rather than a documentation note — and makes the SQLite-side
  bootstrap lock unconditionally sufficient (only one process ever enters
  it), rather than a race mitigation.
- `requirements.txt`: add a Postgres driver (`psycopg[binary]`) so
  `DATABASE_PATH=postgresql://...` works immediately if ever set — no
  driver-install step needed later.
- `docs/database.md`: restructure "Concurrency under multiple Gunicorn
  workers" — SQLite's WAL/busy_timeout section now explicitly framed as
  "only relevant because SQLite is forced to one worker; multi-worker
  concurrency itself is handled by not having multiple workers," plus a new
  short section on the Postgres path (advisory lock, multi-worker allowed).
- `docs/decisions.md`: new entry recording *why* single-worker-for-SQLite
  was chosen over migrating Drawbridge to Postgres (references
  `kea-hook-findings.md`).
- `docs/deployment.md`: document the new `WORKERS` env var and the
  SQLite-forces-single-worker behavior.
- **Tests are not rewritten.** Alpha continues testing against SQLite
  exactly as today (`tests/conftest.py`'s `tmp_path`-based fixtures,
  unchanged) — this is what "no complete rewrite" buys: the Postgres branch
  in `db.py` is new code, structurally parallel to the tested SQLite path,
  but isn't itself covered by the existing suite. That's an accepted,
  explicitly-noted gap for alpha, not silently ignored — worth a one-line
  callout in `docs/testing.md`.

## Verification

- `pytest` — full suite green, SQLite path unchanged (~200 tests plus the
  new `test_lease_api.py` and the multiprocessing bootstrap-race test).
- Rebuild `tests/kea-integration` harness (`docker compose up --build -d
  drawbridge postgres kea`); confirm `kea-admin db-init pgsql` succeeds and
  `kea-dhcp4` logs `PGSQL_HB_DB opening PostgreSQL hosts database` and
  `HOSTS_BACKENDS_REGISTERED ... postgresql`.
- Confirm `drawbridge` boots exactly one worker against its default SQLite
  `DATABASE_PATH`, with exactly one bootstrap password printed.
- Register a device, run `simulate_client.py` for: known serial (expect
  DHCPACK + a successful `reservation-add`, no more "Host database not
  available" error), known-via-MAC-fallback/no-Option-61 (expect DHCPACK),
  and unknown serial (expect **no DHCPACK at all** — this is the specific
  behavior that was broken last time and is the main thing to reconfirm).
- `curl -X PUT .../api/provision-complete` to close the loop; confirm
  `/api/log` shows the entry and the device row is gone.