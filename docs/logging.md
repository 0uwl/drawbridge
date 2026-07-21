# ZTP Client Logging

Drawbridge collects logs from ZTP devices during provisioning from two
sources — the script's own structured events, and the device's raw syslog —
and stores both as `DeviceLogEntry` rows, viewable in the GUI filtered by
serial. This is distinct from `ProvisioningLog` (see
[database.md](database.md), "Log Retention & Data Minimisation"): that's a
structured provisioning-*outcome* audit trail (one row per completed/failed
run); this is a raw, high-volume live log feed.

**Decision: in-container rsyslog via s6-overlay, not a sidecar container.**
One image to deploy/update, at the cost of adding a process supervisor to
what was a single-process `Containerfile`. This repo has no
pod/multi-container orchestration anywhere else, so a sidecar would have
introduced that pattern for this one feature; in-container keeps the
poller's DB access in-process (reusing `db.py`'s session machinery
directly) instead of crossing a container boundary.

## Container layer

`ENTRYPOINT ["/init"]` (s6-overlay) supervises three sibling `longrun`
services, defined under `container/s6-rc.d/`:

- **`gunicorn`** — the app itself, unchanged from before.
- **`rsyslog`** — listens for device syslog on UDP+TCP `:10514` (see
  `container/rsyslog-drawbridge.conf`, installed to
  `/etc/rsyslog.d/drawbridge.conf` in the image — not some other top-level
  `/etc` path, since the rsyslog package's own bundled AppArmor profile
  only allows `/etc/rsyslog.conf` and `/etc/rsyslog.d/**`), writes matched
  lines to a named pipe at `/run/rsyslog/devicelog.fifo`.
- **`log-poller`** (`drawbridge/log_poller.py`) — tails that FIFO and
  inserts each line as a `DeviceLogEntry` via the existing
  `app.extensions['db_session_factory']` (the same hook `db.py`'s
  request-scoped `get_session()` is built on, used directly here since the
  poller isn't inside a Flask request).

**Why :10514, not the standard :514**: 514 is a privileged port, and the
image runs as non-root UID 1000 throughout (no `CAP_NET_BIND_SERVICE`, no
root init phase for s6-overlay). The host side stays `:10514` too, rather
than remapping down to the standard `:514` — see
`quadlet/drawbridge.container`'s `PublishPort=10514:10514/udp` and `/tcp`.
Rootless Podman's `rootlessport` helper can't bind a host port below 1024
without a host-wide sysctl change, and forcing `:514` on the host would
also risk colliding with any other syslog daemon already listening there
— so devices' logging destination just needs to be pointed at `:10514`
explicitly instead of the syslog default (see the IOS-XE example below).

**Why a named pipe (`ompipe`), not a plain file (`omfile`)**: a file on
tmpfs would need the poller to also solve rotation/truncation — a real race
against rsyslog concurrently writing. A FIFO has no backing storage: the
kernel pipe buffer gives natural backpressure and needs zero rotation
logic. Trade-off: if rsyslogd restarts, the poller's open read-end sees EOF
and must reopen — handled by a plain retry loop in `log_poller.py`, not new
machinery. No s6 ordering dependency exists between `rsyslog` and
`log-poller` — the poller's `FileNotFoundError` retry absorbs "poller
started before the FIFO exists," and `open()`'s blocking-until-writer
semantics absorb "poller opened before rsyslog."

No unit test covers the supervisor/rsyslog wiring itself (infra, not
logic) — see [deployment.md](deployment.md) for the manual smoke test.

## Data model

```python
class DeviceLogEntry(Base):
    __tablename__ = 'device_logs'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    serial: Mapped[str | None]  # nullable — syslog may arrive before a serial is matched
    source: Mapped[str]         # 'script' | 'syslog'
    message: Mapped[str]
    timestamp: Mapped[str] = mapped_column(default=utcnow_iso)
```

Purged by the same `log_retention_days` `Setting` row `ProvisioningLog`
uses, via the same lazy-purge-on-insert pattern (`queries.py`'s
`add_device_log_entry`/`purge_expired_device_logs`) — not a second,
independently configured retention knob.

**Syslog→serial correlation**: rsyslog's `dblog` template
(`container/rsyslog-drawbridge.conf`) prefixes each line with
`%fromhost-ip%`. `log_poller.py` looks up the active
`ProvisioningSession` whose `ip` matches (`queries.find_active_session_by_ip`,
a linear scan — the in-flight session table is small enough that no index
is warranted) and stamps that session's `serial` on the row if found;
otherwise `serial` stays `null`. Without this, syslog-sourced rows could
never be filtered by device, defeating half the feature's stated purpose.

## API

- **`GET /api/v1/device-logs?serial=<serial>`** (`drawbridge/api/device_logs.py`,
  `@login_required`) — returns `DeviceLogEntry` rows, most recent first,
  optionally filtered by serial. Mirrors `GET /api/v1/log`'s shape
  (`ProvisioningLog`) but is its own route/blueprint, not a repurposing of
  it — different model, different concern.
- **`POST /api/v1/device-logs`** (same file, no auth decorator — same
  posture as `leases.py`'s `provision-request`/`provision-complete`: gated
  by a serial lookup, not caller identity) — called by the ZTP script's
  `log_to_server()`. Body: `{"serial": "...", "message": "..."}`. `source`
  is never client-supplied; the route always stamps `'script'`.
  `'syslog'` rows are written directly by the poller, which already holds
  a DB session in-process — bouncing syslog through this same HTTP
  endpoint would be a pointless extra hop.

## Device script

`scripts/ztp-base.py`'s `log_to_server(serial, message, platform)` shares
`report_status`'s PUT-JSON transport (`_put_json`, the same three-way
C9200CX/other-platform/local-testing dispatch documented in
[decisions.md](decisions.md)). Called from `main()` at three points: start,
after the provision-request decision, and on completion. On real hardware,
the device is also configured to send its own syslog
(`logging host <drawbridge-ip> transport {udp|tcp} port 10514` — the
non-standard port matters here, see "Why :10514" above) to Drawbridge — no
realistic way to unit-test that device-side config without
hardware, documented here as manually verified, same posture as other
C9200CX-dependent behavior in this file.

## GUI

`views/DeviceLogs.vue` (`stores/deviceLogs.ts`), routed at `/device-logs`,
optionally filtered via a `?serial=` query param. Reached either directly
or by clicking a row in `views/Sessions.vue` (the active-session join
point).
