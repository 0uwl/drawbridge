# ZTP Client Logging

Drawbridge collects logs from ZTP devices during provisioning from two
sources — the script's own structured events, and the device's raw syslog —
and stores both as `DeviceLogEntry` rows, viewable in the GUI filtered by
serial. This is distinct from `ProvisioningLog` (see
[database.md](database.md), "Log Retention & Data Minimisation"): that's a
structured provisioning-*outcome* audit trail (one row per completed/failed/
cancelled run); this is a raw, high-volume live log feed.

**Decision: a separate `drawbridge-rsyslog` container in the same pod, not
in-container via a process supervisor.** An earlier version ran rsyslog
in-container under s6-overlay, specifically to avoid introducing a
pod/multi-container pattern this repo had nowhere else. That traded away
more than it bought: s6-overlay's own footguns (env-var stripping requiring
`S6_KEEP_ENV=1`, opaque `/init` boot behavior, a shared "any one dies, kill
everything" `finish` script across three unrelated processes) outweighed
the one-image-to-deploy convenience. Splitting rsyslog into its own
container, in a Podman pod alongside the `drawbridge` app container,
removes the supervisor entirely: each container in the pod runs exactly one
foreground process, and Quadlet/systemd's per-container `Restart=on-failure`
replaces s6's job — with the added benefit that a crashed
`drawbridge-rsyslog` container no longer takes `drawbridge` (and every live
GUI session with it) down with it, the way one s6 service failing used to.

## Container layer

Pod `drawbridge` (`quadlet/drawbridge.pod`, **requires Podman 5.0+** — `.pod`
Quadlet units and the `Pod=` key on `.container` units both landed in that
release) contains two containers, sharing a network namespace but not a
filesystem or container-name DNS:

- **`drawbridge`** (`Containerfile`) — the app itself, `ENTRYPOINT`
  running `gunicorn` directly, no supervisor.
- **`drawbridge-rsyslog`** (`Containerfile.rsyslog`) — a separate,
  **Alpine**-based image (`apk add rsyslog rsyslog-http` — Alpine packages
  `omhttp`'s dependency as `rsyslog-http`; Debian/Ubuntu's rsyslog
  packaging doesn't ship an omhttp-capable build at all), `ENTRYPOINT`
  running `rsyslogd -n -i NONE -f /etc/rsyslog.d/drawbridge.conf` directly.
  Listens for device syslog on UDP+TCP `:10514` (installed to
  `/etc/rsyslog.d/drawbridge.conf` in the image, not some other top-level
  `/etc` path — the rsyslog package's own bundled AppArmor profile only
  allows `/etc/rsyslog.conf` and `/etc/rsyslog.d/**`).

**Why :10514, not the standard :514**: 514 is a privileged port, and both
containers run as non-root UID 1000 throughout (no `CAP_NET_BIND_SERVICE`).
The host side stays `:10514` too, rather than remapping down to the
standard `:514` — see `quadlet/drawbridge.pod`'s `PublishPort=`.
Rootless Podman's `rootlessport` helper can't bind a host port below 1024
without a host-wide sysctl change, and forcing `:514` on the host would
also risk colliding with any other syslog daemon already listening there
— so devices' logging destination just needs to be pointed at `:10514`
explicitly instead of the syslog default (see the IOS-XE example below).

**Why HTTP (`omhttp`), not a named pipe or a second endpoint**: pod members
share a network namespace, so `drawbridge-rsyslog` reaches `drawbridge` at
`https://127.0.0.1:8080` — an ordinary HTTP call, no FIFO, no poller
process, no separate DB session to manage. `container/rsyslog-drawbridge.conf`
POSTs every line straight to the existing `POST /api/v1/device-logs`
endpoint — one unconditional action, no per-vendor branching in rsyslog
config at all (see "Multi-vendor" below). The IP→serial correlation that an
earlier in-process poller used to do lives in the `device-logs` Flask route
itself now, which also runs each message through
`drawbridge/device_events.py`'s `detect_state()` and updates the correlated
session's `state` inline when it matches — no separate
`/api/v1/device-events` endpoint, no second HTTP round-trip per event.

The `omhttp` action connects with `usehttps="on"` but `allowunsignedcerts="on"`
(curl's `CURLOPT_SSL_VERIFYPEER=0`) instead of a `tls.cacert` — it skips
CA-chain validation entirely rather than pointing at Drawbridge's cert, so
`drawbridge-rsyslog` doesn't need that cert (or any volume mount at all —
see `quadlet/drawbridge-rsyslog.container`). rsyslog's own docs call
`allowunsignedcerts` "strongly discouraged... primarily useful only for
debugging or testing," which holds for a real network path, but doesn't
apply here: this connection never leaves the pod's shared loopback network
namespace, so there's no position an attacker could occupy to MITM it.
Hostname verification stays on regardless (not disabled via
`skipverifyhost`) — `drawbridge/tls.py`'s self-signed cert includes a SAN
(`127.0.0.1` + `localhost`) so that check passes against the cert received
over the connection itself, no local file needed either way (a bare-CN
cert, which is all older versions generated, fails that check even when
otherwise trusted).

No unit test covers the container/rsyslog wiring itself (infra, not logic)
— see [deployment.md](deployment.md) for the manual smoke test.

## Data model

```python
class DeviceLogEntry(Base):
    __tablename__ = 'device_logs'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    serial: Mapped[str | None]  # nullable — syslog may arrive before a serial is matched
    source: Mapped[str]         # 'script' | 'syslog'
    message: Mapped[str]
    timestamp: Mapped[str] = mapped_column(default=utcnow_iso, index=True)
```

Purged by the same `log_retention_days` `Setting` row `ProvisioningLog`
uses, via the same lazy-purge-on-insert pattern (`queries.py`'s
`add_device_log_entry`/`purge_expired_device_logs`) — not a second,
independently configured retention knob — but cleared immediately (ahead
of that retention window) on a successful `provision_complete` or when the
device is removed from the allowlist entirely. See
[database.md](database.md), "Log Retention & Data Minimisation", for the
full rule.

**Syslog→serial correlation**: `container/rsyslog-drawbridge.conf`'s
`deviceLogBody` template sends each line as `{"ip": "<fromhost-ip>",
"message": "<msg>"}`. `POST /device-logs` (`drawbridge/api/device_logs.py`)
looks up the active `ProvisioningSession` whose `ip` matches
(`queries.find_active_session_by_ip`, a linear scan — the in-flight session
table is small enough that no index is warranted) and stamps that
session's `serial` on the row if found; otherwise `serial` stays `null`.
Without this, syslog-sourced rows could never be filtered by device,
defeating half the feature's stated purpose. Every correlated call also
bumps that session's `last_seen_at` (see [database.md](database.md),
"Stale sessions") — any log line at all is evidence the device is still
alive, whether or not it also matches a state-detection trigger.

## Multi-vendor

State detection from syslog text lives in `drawbridge/device_events.py`, a
plain Python module — not rsyslog config — specifically because Drawbridge
is expected to eventually handle Juniper ZTP too: a vendor-keyed Python
trigger table is unit-testable per vendor and keeps "which strings mean
what state" knowledge in one reviewed/tested place, instead of sprawling
`if`/`else if` vendor branches across an ops config that isn't code-reviewed
or tested the same way. `detect_state()` checks each vendor's trigger tuple
in order (Cisco IOS-XE's today; a `JUNIPER_TRIGGERS` placeholder documents
where the next one plugs in) and returns the first matching state, or
`None` for a routine line. Adding a new vendor touches only that one file —
`container/rsyslog-drawbridge.conf` stays exactly as it is, since its job
is just "forward every line as JSON," genuinely vendor-agnostic.

## API

- **`GET /api/v1/device-logs`** (`drawbridge/api/device_logs.py`,
  `@login_required`) — returns `DeviceLogEntry` rows, most recent first.
  Optional `serial` query filter. Optional `after_id` returns only rows
  newer than the given id, for polling clients that already hold everything
  up to that point (see "GUI" below).
- **`POST /api/v1/device-logs`** (same file, no auth decorator — same
  posture as `leases.py`'s `provision-request`/`provision-complete`: gated
  by lookup, not caller identity) — two callers, two body shapes. The ZTP
  script's `log_to_server()` sends `{"serial": "...", "message": "..."}`,
  stamped `source='script'`; the serial must resolve to a known `Device` or
  `ProvisioningSession`. `drawbridge-rsyslog`'s `omhttp` action sends
  `{"ip": "...", "message": "..."}`, stamped `source='syslog'` and
  correlated to a session as described above — no match just means the
  row's `serial` stays null, not a rejection. Either shape may also include
  an explicit `state`: the script knows its own lifecycle steps (fetching a
  config, applying it) that no syslog line would ever announce, so it can
  declare the state directly instead of wording a message to match a
  `device_events` trigger — must be one of `PROVISIONING_STATES`
  (`422 invalid_state` otherwise), and skips `detect_state()` entirely when
  present.

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
point). Polls `GET /device-logs` every 2s via the shared
`composables/usePolling.ts` — the first call does a full fetch, every call
after that uses `after_id` to fetch and prepend only what's new, so a
long-running session's log view doesn't re-fetch and re-render its whole,
growing history on every tick.
