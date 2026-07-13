**Superseded.** The DHCP-level Kea hook gate investigated here was
abandoned in favor of a script-level gate — see
[decisions.md](decisions.md) ("Provisioning gate moved from DHCP-level to
script-level"). Kept for reference; the findings below (native-hook
requirement, the `unpark()`/DROP bug, `run_script`'s async-only limitation,
`host_cmds`+Postgres requirement) remain accurate Kea behavior. §5 (the
SQLite multi-worker bootstrap race) is unrelated to Kea and still directly
applies — see [database.md](database.md).

# Kea Hook Implementation — Findings

Notes from actually building and running the `leases4_committed` hook and a
containerized Kea test harness (alpha.md step 6), against real Kea 3.0.3 and
real Gunicorn — not assumptions carried over from the original design docs.
Several things the original design took for granted turned out to be wrong
or incomplete once tested against the real software. This document exists
so the next implementation pass doesn't have to rediscover any of this.

## 1. The hook cannot be `run_script`

Kea's built-in `libdhcp_run_script.so` hook runs **asynchronously by
default**, and its own documentation states synchronous mode ("wait for the
script, honor its exit code as a next-step decision") **"is not implemented
and enabling synchronous calls to external script is not supported."** There
is no exit-code → DROP/CONTINUE mapping at all. Since the fail-closed gate
("unknown or timed-out device → Kea drops the packet") is the core security
property of this system, `run_script` cannot implement it, full stop.

**Conclusion:** the callout must be a natively compiled Kea hook library
using the hooks SDK's `ParkingLotHandle` API. This matches what
`docs/decisions.md` already said ("the PARK mechanism handles the
synchronisation"), but that line alone undersold how much real C++ work it
implies — see below.

## 2. `ParkingLotHandle::unpark()` does not honor a DROP decision

This is the single most important, least obvious finding, and it silently
broke the security gate during testing (an *unregistered* device still got a
DHCPACK).

Kea's parking model: the **server** parks the packet before invoking
callouts on `leases4_committed`; a callout that wants to do async work calls
`reference()`, returns with `setStatus(NEXT_STEP_PARK)`, then later — from a
worker thread — is expected to signal completion.

The mistake: calling `parking_lot->unpark(query4)` **always** resumes normal
packet processing (i.e. sends whatever DHCPACK Kea had already prepared
before parking), regardless of what `CalloutHandle::setStatus()` was set to.
`setStatus(NEXT_STEP_DROP)` has no effect on an already-parked packet's
eventual fate.

The actual "abandon this packet, send nothing" primitive is
**`ParkingLotHandle::drop()`** — it removes the parked object *without*
invoking the resume callback at all, so no response is ever sent. The
correct pattern:

```cpp
if (approved) {
    handle->setStatus(CalloutHandle::NEXT_STEP_CONTINUE);
    parking_lot->unpark(query4);   // resumes -> DHCPACK
} else {
    handle->setStatus(CalloutHandle::NEXT_STEP_DROP);
    parking_lot->drop(query4);     // abandoned -> no response
}
```

Verified against real Kea 3.0.3: before this fix, an unregistered device
still received a DHCPACK; after, it correctly gets no response at all.

## 3. `Pkt4` owns a persistent `CalloutHandle` — this is what makes the
   worker-thread pattern safe

`Pkt` (base of `Pkt4`) inherits from `hooks::CalloutHandleAssociate`, which
holds one `CalloutHandlePtr` for the packet's *entire* processing lifetime
across all hook points ("Subsequent calls to this method always return the
same handle" — `hooks/callout_handle_associate.h`). Concretely,
`query4->getCalloutHandle()` returns the exact same object as the `handle`
reference passed into the `leases4_committed` callout.

This is what makes it safe to capture `query4` (a `Pkt4Ptr`, i.e. a
`boost::shared_ptr`) by value into a detached worker thread: the shared_ptr
keeps both the packet and its associated `CalloutHandle` alive across the
async HTTP call, and mutating `query4->getCalloutHandle()->setStatus(...)`
from that thread is safe and meaningful — it's the same handle Kea consults.

## 4. Serial/MAC allowlist gap (found and fixed independently of the hook)

`docs/architecture.md` documents that IOS XE alternates DHCP Option 61
(client-id) between the device serial and its MAC across DHCPDISCOVER
retries, and says the allowlist should match either. But
`queries.get_device()` / `api/leases.py:lease_event()` only looked up
`Device` by primary key (`serial`) — a retry that sent the MAC as Option 61
would 404 against an already-registered device.

Fixed: `get_device_by_serial_or_mac()` (serial lookup first, falls back to
matching on `mac`), wired into `lease_event()`. Also fixed a second-order
bug this exposed: `create_provisioning_session()` was keying the session on
the raw request `serial` field rather than the resolved `device.serial` —
on a MAC-fallback approval, the session would be stored under the MAC, and
the device's later `/api/provision-complete` callback (which reports its
real serial) would then 404 against it. Both are fixed and covered by
`tests/test_lease_api.py`.

## 5. SQLite first-run bootstrap race across Gunicorn workers

This is **not a rare edge case** — it reproduced on nearly every attempt
once actually run through the real production entrypoint (Gunicorn, 4
workers, per `Containerfile`/`gunicorn.conf.py`) against a genuinely fresh
`DATABASE_PATH`. It was invisible before now because `pytest` and
`scripts/dev.sh`/`flask run` never exercise multiple real OS processes
racing on one fresh SQLite file — that's specifically a Gunicorn-without-
`--preload` behavior (every worker independently imports and calls
`create_app()` after fork, per `docs/decisions.md`'s existing note on
post-fork Engine creation).

What happens: every worker's `_is_first_run()` check (file-existence based)
can independently see "missing," and all of them race to run
`Base.metadata.create_all()` and insert the same seed rows / bootstrap the
same admin user. Symptoms observed directly: worker crashes on
`IntegrityError`/`OperationalError` (unhandled), multiple different
bootstrap passwords printed to stdout when only one user row could possibly
exist, and — worst — a run where **zero** admin users ended up created at
all despite every worker reporting success, because the race also hits
`Base.metadata.create_all()` itself (`OperationalError: table already
exists`), not just the seed insert.

Ad hoc `except IntegrityError` / `except OperationalError` patches around
individual statements were tried and are **not sufficient** — the race has
more than one shape (schema creation, per-setting seed inserts, admin
bootstrap) and whack-a-mole exception handling around each one is fragile
and was observed to still occasionally lose data silently.

**Fix implemented:** wrap the entire first-run sequence (schema creation +
seeding + admin bootstrap + commit) in a single `fcntl.flock`-based advisory
lock (`DATABASE_PATH + '.bootstrap-lock'`), so only one worker is ever
inside it at a time; by the time the next worker acquires the lock, the
schema/seed rows/admin user already exist and it correctly does nothing.
Verified with a real `multiprocessing`-based test spawning 4 actual OS
processes against a barrier, asserting exactly one admin row and one
settings row survive (`tests/test_db.py::test_concurrent_first_run_bootstraps_exactly_once`,
passed 6/6 consecutive runs plus every run under the real containerized
harness afterward).

**This is the direct motivation for the user's decision (recorded in
`docs/decisions.md`) to migrate Drawbridge's own application database off
SQLite to PostgreSQL** — a real client/server RDBMS does not have this
class of problem (concurrent DDL/DML from independent processes against a
single file is fundamentally what SQLite is weak at, no matter how much
locking is layered on top).

## 6. Kea Control Agent's loopback binding is a real topology assumption,
   not just a security nicety

`docs/kea.md` documents Control Agent bound to `127.0.0.1:8081` — this is
correct for the actual production topology (Kea runs natively on an Ubuntu
host; Drawbridge runs in a rootless Podman container reaching the *host's*
loopback via Quadlet's `AddHost: keahost=host-gateway`). In a
container-per-service test harness, though, "loopback" means the Kea
container's own network namespace — completely unreachable from a sibling
`drawbridge` container. This isn't a bug in `kea-ctrl-agent.conf`; it's a
mismatch between the harness's topology and production's. The harness
worked around it with a `sed` patch applied only to the *running* copy
inside the `kea` container's entrypoint, leaving the checked-in
`kea/kea-ctrl-agent.conf` untouched and production-accurate.

## 7. `host_cmds`' `reservation-add`/`reservation-del` require a real
   hosts-database — confirmed no SQLite/memfile option exists

This is a hard requirement of Kea itself, not a harness inconvenience, and
it means the *existing* `drawbridge/kea.py` design (which calls
`reservation-add`/`reservation-del` unconditionally) cannot work against
the `kea-dhcp4.conf` written for step 6, which had no `hosts-database`
configured at all.

Confirmed directly:
- Without a `hosts-database`, Kea returns `Unable to open database: The Kea
  server has not been compiled with support for host database type:
  <type>` (misleading — it's not a compile-time issue, it's that the
  relevant backend hook, e.g. `libdhcp_pgsql.so`, wasn't loaded).
- `host_cmds`' `reservation-add` returns `Host database not available,
  cannot add host` without one configured.
- Getting it working requires **both** a `hosts-database` block **and**
  explicitly loading the matching backend as a `hooks-libraries` entry
  (e.g. `libdhcp_pgsql.so`) — installing the package alone isn't enough.
- Verified end-to-end against a real PostgreSQL 16 container: schema
  initialized via `kea-admin db-init pgsql`, `kea-dhcp4` started
  successfully against it (`PGSQL_HB_DB opening PostgreSQL hosts
  database`), host backend correctly registered
  (`HOSTS_BACKENDS_REGISTERED ... postgresql`).
- Not yet verified: whether `kea-admin db-init` is safely re-runnable
  against an already-initialized database (needed for a repeatable harness
  startup script) — this was the point at which the investigation was
  paused to regroup.

## Where this leaves things

Two independent, confirmed-necessary architecture changes fell out of this
investigation, beyond "write the hook":

1. **Kea's own hosts-database must be PostgreSQL** (or MySQL — PostgreSQL
   was the one verified). This is required for `reservation-add`/`del` to
   function at all, in production as much as in any test harness.
2. **Drawbridge's own application database should migrate from SQLite to
   PostgreSQL**, per the user's direction, primarily to eliminate the class
   of multi-worker races found in §5 rather than continuing to layer
   file-locking workarounds on SQLite.

A revised implementation plan covering both, plus a corrected hook and test
harness, is needed before resuming implementation.
