# ZTP Client Logging

Drawbridge collects logs from ZTP devices during provisioning from two
sources — the script's own structured events, and the device's raw syslog —
and stores both as `DeviceLogEntry` rows, viewable in the GUI filtered by
serial. This is distinct from `ProvisioningLog` (see
[database.md](database.md), "Log Retention & Data Minimisation"): that's a
structured provisioning-*outcome* audit trail (one row per completed/failed/
cancelled run); this is a raw, high-volume live log feed.

Kea's own DHCP server logs are a separate, third source, covered in "Kea
server logs" below — deliberately not folded into `DeviceLogEntry` or its
`:10514` listener, since Kea's logs carry no device correlation at all.

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
release) has `drawbridge-rsyslog` as one of its members, sharing a network
namespace with the app container (and the unrelated `drawbridge-bootstrap`
container — see [deployment.md](deployment.md)) but not a filesystem or
container-name DNS:

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
`http://127.0.0.1:8078` — an ordinary HTTP call, no FIFO, no poller
process, no separate DB session to manage. `container/rsyslog-drawbridge.conf`
POSTs every line straight to the existing `POST /api/v1/device-logs`
endpoint — one unconditional action, no per-vendor branching in rsyslog
config at all (see "Multi-vendor" below). The IP→serial correlation that an
earlier in-process poller used to do lives in the `device-logs` Flask route
itself now, which also runs each message through
`drawbridge/device_events.py`'s `detect_state()` and updates the correlated
session's `state` inline when it matches — no separate
`/api/v1/device-events` endpoint, no second HTTP round-trip per event.

**Plain HTTP, not HTTPS**: `127.0.0.1:8078` is Gunicorn's own internal-only
bind (v0.3.2 — `drawbridge-nginx` terminates TLS for external traffic
instead, see [deployment.md](deployment.md) "TLS"). There's no cert on
this hop at all, so nothing for `drawbridge-rsyslog` to skip-verify or
pin — it never leaves the pod's shared loopback network namespace, so
there's no position an attacker could occupy to MITM it either way.

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

## Kea server logs

A second, independent pipeline (v0.3.1) for Kea's own DHCP server logs
(lease grants, config errors) — not device-authored content, and not
folded into anything above.

**Why a separate pipeline, not a `source='kea'` value on `DeviceLogEntry`
sharing `:10514`:** device syslog is IP-correlated to a
`ProvisioningSession` (`find_active_session_by_ip`); Kea's own logs have no
serial/session/IP-of-a-provisioned-device relationship to correlate against
at all — they're host-daemon operational logs. Branching on `programname`
inside `container/rsyslog-drawbridge.conf` to route to a different REST
path would work, but cuts against this file's own stated design principle
above ("no per-vendor branching in rsyslog config at all — that knowledge
lives in Python"): reusing one rsyslog config for two structurally
different jobs is the same shape of mistake in a different place. A second
listener, in the same container, keeps each pipeline a straight line.

**Kea-side:** unlike device syslog (which devices are configured to send
directly), Kea's logging backend (log4cplus) only supports **local** syslog
output — no direct remote host:port option. `kea/kea-dhcp4.conf` and
`kea/kea-ctrl-agent.conf` both carry a `"loggers"` block outputting to
`syslog:local0` — facility `local0` specifically so the host-side rule
below can select just these lines without guessing at `programname`.

**Host-side forwarding:** Kea itself is not containerized (see
[decisions.md](decisions.md) — rootless Podman can't do the raw DHCP
broadcast socket Kea needs), so it runs as a native systemd service and its
`local0` syslog output needs one hop to reach the pod. `kea/rsyslog-kea-forward.conf`
(repo-tracked, installed by `install.sh` to `/etc/rsyslog.d/49-drawbridge-kea.conf`,
alongside `kea/*.conf` going to `/etc/kea/`) matches `local0.*` and forwards
via `omfwd` (`@@127.0.0.1:10515`, TCP) — deliberately without a `stop`
directive after it, so these lines still reach the host's normal default
logging (`/var/log/syslog` or equivalent) too; this rule adds a copy into
Drawbridge, it doesn't redirect Kea's local logging away.

**Container layer:** `container/rsyslog-drawbridge.conf` binds a second
`imtcp` input on **`:10515`** to its own ruleset (`kealogs`), kept separate
from the default ruleset the `:10514` device-syslog input uses. Its
`omhttp` action POSTs `{"message": "<msg>"}` (no `ip` property — nothing to
correlate) to `POST /api/v1/kea-logs`, same plain-HTTP posture (no TLS on
this loopback-only hop at all — see "Why HTTP" above) as the device-logs
action. `quadlet/drawbridge.pod` publishes `10515:10515/tcp` alongside the
existing `10514` ports.

**Data model:**

```python
class KeaLogEntry(Base):
    __tablename__ = 'kea_logs'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message: Mapped[str]
    timestamp: Mapped[str] = mapped_column(default=utcnow_iso, index=True)
```

No `serial` column — see "Why a separate pipeline" above. Purged by the
same `log_retention_days` `Setting` row, via the same
lazy-purge-on-insert pattern (`queries.py`'s `add_kea_log_entry`/
`purge_expired_kea_logs`) as `DeviceLogEntry`/`ProvisioningLog` — no
early-clear-on-success behavior, since a Kea log line was never tied to one
device's run to begin with. See [database.md](database.md), "Log Retention
& Data Minimisation".

**API:**

- **`GET /api/v1/kea-logs`** (`drawbridge/api/kea_logs.py`,
  `@login_required`) — returns `KeaLogEntry` rows, most recent first.
  Optional `after_id`, same polling semantics as `GET /device-logs`. No
  `serial` filter — there's nothing to filter by.
- **`POST /api/v1/kea-logs`** (same file, no auth decorator — same posture
  as `POST /device-logs`: the rsyslog forwarder has no operator session to
  authenticate with) — `{"message": "..."}` only.

**GUI: not built yet.** This release only gets Kea's logs into a
queryable table/endpoint — where and how to surface them (a new tab, folded
into an existing view, something else) is an open question for a follow-up,
deliberately left unresolved here rather than guessed at.
