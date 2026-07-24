# Architecture

## What This Project Is

A Zero Touch Provisioning (ZTP) system for Cisco IOS XE devices. It replaces manual Day 0 configuration by automatically
serving a Python provisioning script to devices when they first boot with no
startup config.

This is a hardened classic ZTP implementation, not Cisco's sZTP (RFC 8572).
The decision to avoid sZTP was deliberate — sZTP requires per-device Ownership
Vouchers from Cisco's MASA server, a Pinned Domain Certificate, and significant
PKI infrastructure. The security model here achieves comparable protection for
an internal, physically isolated provisioning VLAN through:

- Kea DHCP pre-authorisation gate (lease withheld until Drawbridge approves)
- Serial number / client-id allowlisting
- HTTPS script delivery with server certificate validation inside the script
- SHA-256 hash verification of images and config payloads (planned — see [beta.md](../beta.md))
- Provisioning VLAN isolation — devices can only reach the Drawbridge server

Drawbridge is **not** an inventory management system. It is not the source
of truth for "what devices does this org own" — it only needs to know about
a device for the brief window between registration and provisioning. Serial
numbers and MAC addresses are deliberately not retained long-term; see
[database.md](database.md) ("Log Retention & Data Minimisation").

## System Architecture

```
Provisioning VLAN
       │
       ▼
┌─────────────────────────────────────────────────────┐
│  Ubuntu/Debian host (amd64/arm64, e.g. Raspberry Pi)│
│                                                     │
│  ┌─────────────────────┐                            │
│  │  Kea DHCPv4         │  native systemd service,   │
│  │  port 67 (UDP)      │  vanilla config - no       │
│  │                     │  hooks, no host            │
│  │                     │  reservations              │
│  └─────────────────────┘                            │
│  ┌─────────────────────┐                            │
│  │  Kea Control Agent  │  operator diagnostics only │
│  │  127.0.0.1:8081     │  (kea-shell) - Drawbridge  │
│  └─────────────────────┘  never calls it            │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │  Drawbridge container (rootless Podman)     │    │
│  │  image: localhost/drawbridge:latest         │    │
│  │                                             │    │
│  │  Flask app, port 8080 (DRAWBRIDGE_PORT)     │    │
│  │  Devices phone home here directly           │    │
│  │  (/api/provision-request)                   │    │
│  │                                             │    │
│  │  /app/data     (Database, certs)            │    │
│  │  /app/files    (images/configs)             │    │
│  └─────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────┐    │
│  │  drawbridge-bootstrap container              │    │
│  │  image: localhost/drawbridge-bootstrap:latest│    │
│  │                                             │    │
│  │  busybox httpd, plain HTTP, port 8090       │    │
│  │  Serves scripts/ztp-base.py (baked in at    │    │
│  │  build time) — the one fetch that happens   │    │
│  │  before any script code can validate a cert │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  Host bind mounts:                                  │
│    ~/.local/share/drawbridge/data/  -> /app/data/   │
│    ~/.local/share/drawbridge/files/ -> /app/files/  │
└─────────────────────────────────────────────────────┘
```

## DHCP Flow

Kea runs no custom hooks and no host reservations. The allow/deny gate
lives in the ZTP script itself, not at the DHCP layer (see
[decisions.md](decisions.md) for why: a native Kea hook was built and
found broken in a security-relevant way, and for this threat model — a
physically isolated provisioning VLAN, not internet-facing — withholding
the DHCP lease itself buys little real protection against a capable
attacker anyway). Kea does use native client classification on Option 60
(vendor-class-identifier) to admit only Cisco/Juniper-looking clients to
the pool and hand each vendor its own DHCP options (see
[kea.md](kea.md)) — Cisco and Juniper ZTP boot differently and need
different options, which is what this is actually for, not access control.
Only the Cisco path has real options configured for alpha; Juniper is
admitted to the pool already but has no ZTP support built yet. Option 60
is client-supplied and trivially spoofable regardless, so this is never a
substitute for the script-level gate below.

1. IOS XE device boots with no startup config, sends DHCPDISCOVER
2. Kea leases an address from the dynamic pool to any client matching the
   vendor-class filter and, for Cisco, returns Option 67 pointing at the
   one fixed ZTP script — every matching Cisco device gets the same script,
   registered or not
3. Device fetches the ZTP script over **plain HTTP**, from the separate
   `drawbridge-bootstrap` container on `:8090` — not from Drawbridge's own
   HTTPS listener. This fetch happens before any script code has run, so
   there's no way to pre-establish trust for a self-signed cert ahead of
   it; whether the boot agent's plain-HTTP fetch itself behaves as assumed
   is still unconfirmed without lab hardware (see [decisions.md](decisions.md),
   "HTTPS cert trust on C9200CX"). Every *subsequent* device-initiated
   request (phone-home, completion callback) goes back over real HTTPS
   against Drawbridge's own listener, with server cert verification: on
   C9200CX, via an IOS XE trustpoint imported by the script itself before
   its first HTTPS call (Guestshell has no network stack of its own there);
   on other platforms, directly in Python via
   `ssl.create_default_context(cadata=...)`. Payload hash verification is
   still planned, not yet implemented (see [beta.md](../beta.md)).
4. Script's first action: reads its own serial via `show version`, calls
   `GET /api/provision-request?serial=...`
5. Drawbridge checks the SQLite allowlist by serial — known → 200 + a
   `ProvisioningSession` row is created; unknown → 404
6. On 404 (or if Drawbridge is unreachable): script exits cleanly, no
   further action — the device is left with its generic DHCP lease and
   nothing else, no image/config/real script logic runs
7. On 200: script proceeds with real provisioning (image/config download,
   hash verification, config push — a later phase; see alpha.md step 5)
8. Script reports completion by writing a JSON status file and issuing
   `copy flash:status.json https://<drawbridge>/api/provision-complete` (IOS XE
   `copy` sends a PUT — see [decisions.md](decisions.md) "C9200CX network stack isolation")
9. Drawbridge writes a `ProvisioningLog` row (time, image, config file) and
   deletes the `ProvisioningSession` row — the `Device` allowlist row
   itself is **not** touched here; it persists until an operator explicitly
   `DELETE`s it (which itself refuses while a session is still active —
   that's what actually prevents concurrent/duplicate provisioning, not
   anything DHCP-side)

## Repository Layout

```
drawbridge/
├── CLAUDE.md                  <- project index, links into docs/
├── README.md
├── docs/                      <- detailed design docs (this file and siblings)
├── Containerfile               <- builds localhost/drawbridge:latest (multi-stage: builds frontend/, then the Flask image)
├── Containerfile.rsyslog       <- builds localhost/drawbridge-rsyslog:latest (Alpine + rsyslog, see logging.md)
├── Containerfile.bootstrap     <- builds localhost/drawbridge-bootstrap:latest (busybox httpd serving the ZTP script, see deployment.md)
├── frontend/                  <- Vue 3 + Vite admin UI, baked into drawbridge/static at build time (see frontend.md)
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── main.js
│       └── App.vue
├── quadlet/                    <- Podman Quadlet units, one pod + three containers (see deployment.md)
│   ├── drawbridge.pod
│   ├── drawbridge.container
│   ├── drawbridge-rsyslog.container
│   └── drawbridge-bootstrap.container
├── kea/
│   ├── kea-dhcp4.conf         <- Kea DHCPv4 configuration (vanilla — no hook)
│   └── kea-ctrl-agent.conf    <- Kea Control Agent (REST API, 127.0.0.1:8081; operator diagnostics only)
├── drawbridge/
│   ├── __init__.py
│   ├── main.py                <- Flask app factory and entry point
│   ├── api/
│   │   ├── leases.py          <- GET /api/provision-request (called by the ZTP script's phone-home step)
│   │   ├── devices.py         <- CRUD for device allowlist
│   │   ├── files.py           <- File management endpoints
│   │   ├── auth.py            <- login/logout, current-user endpoints
│   │   ├── users.py           <- admin CRUD for operator accounts
│   │   └── settings.py        <- admin get/set of log-retention setting
│   ├── db.py                  <- SQLAlchemy engine/session setup and init_db()
│   ├── models.py              <- Device, ProvisioningLog, Setting, User SQLAlchemy models
│   ├── auth.py                <- Flask-Login setup (LoginManager, user_loader,
│   │                             password hashing); future home for the SAML
│   │                             SP integration (see authentication.md)
│   └── static/                <- built frontend output (generated, gitignored — see frontend.md)
├── scripts/
│   └── ztp-base.py            <- Base ZTP script served to IOS XE devices
├── tests/
│   ├── conftest.py
│   ├── test_lease_api.py
│   ├── test_devices_api.py
│   ├── test_auth_api.py
│   └── test_kea_client.py
└── requirements.txt
```
