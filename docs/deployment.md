# Deployment

## Network isolation — strongly recommended

**Drawbridge's security guarantees only hold if the provisioning VLAN is
physically/logically isolated and access to it is restricted by switch and
firewall policy.** Drawbridge cannot enforce this itself and will run
without it — but doing so knowingly weakens what it protects against, so
read this before deploying, especially before exposing Drawbridge beyond a
lab network.

`/api/provision-request` (the device phone-home call) is deliberately
unauthenticated: at the moment a device first contacts the network, it
holds no credential Drawbridge could check — only its own serial number,
which is printed on the chassis and not a secret. That means **anyone who
can send or observe traffic on the provisioning VLAN can enumerate which
serials are registered**, by watching the 200-vs-404 response the same way
a legitimate device would. This is a structural property of Classic ZTP as
implemented here, not a bug — see [decisions.md](decisions.md) ("No sZTP")
for why. No token or secret handed to the device over that same first
contact can fix it either: whatever value would be checked has to cross
the wire during that same unauthenticated conversation, so it's exactly as
observable/replayable to an eavesdropper as the serial itself. The only
mechanism that actually closes this gap is a hardware-rooted device
identity proven cryptographically before any network contact (Cisco SUDI /
IEEE 802.1AR IDevID, via RFC 8572's Ownership Vouchers and MASA
infrastructure) — which Drawbridge deliberately does not use, per
[decisions.md](decisions.md).

Drawbridge protects what it can at the application layer:
- **Serial number allowlisting** — gates which devices get provisioned at all.
- **HTTPS transport** — Drawbridge terminates its own TLS (self-signed by
  default) for all device- and GUI-facing traffic; see "TLS" below.
- **SHA-256 hash verification** of served images and config payloads
  (planned — see [beta.md](../beta.md)).

These protect *what* gets provisioned and *to whom* it's addressed. None of
them can protect against a device already on the provisioning VLAN passively
watching or probing the exchange — that was never something application
code running on the server could enforce.

**Closing that gap is the deployment's job, not something Drawbridge can
check or enforce at runtime:**
- No routing between the provisioning VLAN and any untrusted network.
- Port security / 802.1X on switch ports serving the VLAN, so arbitrary
  devices can't simply plug in.
- DHCP snooping and dynamic ARP inspection, so a rogue device can't spoof
  the DHCP server or intercept another device's unicast traffic.
- No hubs, unmanaged switches, or mirrored/monitor ports on the segment.

**Consequence of skipping this:** Drawbridge will run fine on a flat or
untrusted network — nothing fails or refuses to start — but the
allowlist/HTTPS/hash protections above stop being the actual boundary
between a trusted and untrusted device. Anyone who can reach the
provisioning VLAN can enumerate registered serials, and (depending on
what else that network reaches) potentially reach `/api/provision-request`
from further away than intended. Treat the isolation controls above as
part of the deployment, not an optional hardening step layered on later.

## Container

**Requires Podman 5.0+.** The pod runs as two images, described by three
Quadlet unit files (below) — `.pod` Quadlet units, and the `Pod=` key on
`.container` units, both landed in Podman 5.0. Older Podman fails with
`unsupported key 'Pod' in group 'Container'`.

**`Containerfile`** builds `localhost/drawbridge:latest` as a multi-stage,
multi-arch (`linux/amd64`, `linux/arm64` — e.g. 64-bit Raspberry Pi OS)
build:
- Stage 1 (`node:22-slim`, pinned to `--platform=$BUILDPLATFORM`): builds the
  Vue frontend (`frontend/`) with `npm ci && npm run build`. Pinned to the
  build host's own arch rather than the target one, since the output is
  arch-independent JS/CSS and doesn't need to run under QEMU emulation. See
  [frontend.md](frontend.md).
- Stage 2 (`python:3.12-slim`, the final image, built for whichever
  `--platform` is requested):
  - Non-root user `drawbridge` (UID 1000) created in image
  - `COPY --from=` pulls the built frontend assets from stage 1 into
    `drawbridge/static/` — Node never ships in the final image
  - `ENTRYPOINT` runs `gunicorn` directly — no process supervisor. Binds
    `0.0.0.0:$DRAWBRIDGE_PORT` (default `8080`, via `-c
    drawbridge/gunicorn.conf.py` — Gunicorn does not discover a config file
    nested under a subdirectory on its own)
  - `/app/data` and `/app/files` are mount points — do not COPY content there
  - Root filesystem is read-only at runtime; `/tmp` and `/run` are tmpfs

**`Containerfile.rsyslog`** builds `localhost/drawbridge-rsyslog:latest` —
a separate, single-stage Alpine image (`apk add rsyslog rsyslog-http`),
`ENTRYPOINT` running `rsyslogd` directly, also no supervisor. Listens on
**:10514** inside the container, not the standard :514 — 514 is a
privileged port and both images run as non-root UID 1000 throughout (no
`CAP_NET_BIND_SERVICE`). See [logging.md](logging.md) for the full
container-layer and rsyslog-config design.

**Quadlet**, at `~/.config/containers/systemd/`, run as whichever user
invokes it — there's no dedicated `drawbridge` system user:
- `quadlet/drawbridge.pod` — owns the pod's shared network namespace,
  `PublishPort=` (`8080` and `10514/udp`+`/tcp`), and `UserNS=keep-id` for
  both member containers' bind mounts.
- `quadlet/drawbridge.container` — the app, `Pod=drawbridge.pod`; otherwise
  the same `Volume=`/`Environment=`/`ReadOnly=true`/`Tmpfs=` as before the
  pod split.
- `quadlet/drawbridge-rsyslog.container` — the syslog collector,
  `Pod=drawbridge.pod`, read-only mount of the data volume (just for
  Drawbridge's TLS cert, see [logging.md](logging.md)), `Restart=on-failure`
  independent of the app container — a crashed `drawbridge-rsyslog` no
  longer takes `drawbridge` down with it, unlike the old single-container/
  s6 setup.

[install.sh](../install.sh) installs all three there automatically for the
user running the script (run it as yourself, not as root/via `sudo` — it
calls `sudo` itself only for the steps that need it); if a unit is already
present and differs, it prompts to back up the old one before overwriting
rather than silently skipping or clobbering it.

Drawbridge is expected to run as a rootless Podman pod with the same
permissions as the invoking user. The data directories used for the
containers must therefore be owned by that user. It's recommended to create a
folder under that user's own XDG data dir, not a root-owned path like `/srv`,
so no `sudo`/`chown` is needed.

Must exist before starting:
```bash
mkdir -p ~/.local/share/drawbridge/{data,files}
```

Then, after editing `SECRET_KEY` (and `ADMIN_PASSWORD` or `LoadCredential=`)
in the installed `drawbridge.container` unit:
```bash
systemctl --user daemon-reload && systemctl --user start drawbridge-pod.service
```
Starting the pod service starts both member containers together.

## TLS

Drawbridge terminates its own TLS by default — both GUI and device-facing
(ZTP phone-home, file downloads) traffic go through the same HTTPS listener.
On first run, if `TLS_CERT_PATH`/`TLS_KEY_PATH` don't already exist,
`drawbridge/tls.py` generates a self-signed cert/key pair there; an operator
who mounts their own cert/key pair at those paths instead (e.g. a real
ACME-issued cert, or a shared org CA) has it used as-is — nothing is
overwritten if the files are already present.

The generated cert includes a SAN (`127.0.0.1` + `localhost`), needed so the
`drawbridge-rsyslog` container's `omhttp` action can verify Drawbridge's
cert when it connects to `https://127.0.0.1:8080` inside the pod's shared
network namespace — a bare-CN cert fails libcurl's hostname check even when
otherwise trusted. **Upgrading from a pre-pod install:** delete
`data/tls/cert.pem` and `data/tls/key.pem` so `ensure_cert()` regenerates
them with the SAN on next start; an existing no-SAN cert is not replaced
automatically.

An operator who wants a "real" ACME-issued cert for browser convenience may
put their own reverse proxy in front of the GUI path only, re-terminating/
re-encrypting to Drawbridge's own listener. ZTP devices always talk directly
to Drawbridge's own listener (self-signed or org-mounted cert) — never
through that optional proxy: Option 67's boot-file URL points at Drawbridge
directly, and isolated provisioning VLANs generally can't complete ACME
challenges anyway.

**Device-side cert trust needs a matching CA cert mounted/embedded on the
device side too**, since a self-signed server cert isn't trusted by anything
out of the box. `scripts/ztp-base.py`'s `DRAWBRIDGE_CA_CERT_PEM` constant
(hand-maintained, same posture as `DRAWBRIDGE_HOST`) must be set to
Drawbridge's actual cert (or its issuing CA) before the script is used
against real hardware — see [decisions.md](decisions.md) for how this gets
consumed on each platform branch.

**Local development:** set `TLS_DISABLED=1` to skip TLS entirely and run
Gunicorn as plain HTTP — a dev convenience so a local `flask run`/`dev.sh`
session doesn't need a trusted cert. This also relaxes
`SESSION_COOKIE_SECURE` so login still works over plain HTTP. **Never set
this in a deployed/Quadlet config** — the deployed container always
terminates TLS.

## Device Syslog Collection

See [logging.md](logging.md) for the full design (the `drawbridge-rsyslog`
pod member, its `omhttp`-to-`POST /api/v1/device-logs` path, the
`DeviceLogEntry` schema, and the two `/api/v1/device-logs` routes). The pod
publishes both UDP and TCP on **`:10514`, not the standard `:514`**
(`quadlet/drawbridge.pod`):

```ini
PublishPort=10514:10514/udp
PublishPort=10514:10514/tcp
```

This is deliberate, not just an in-container detail: rootless Podman's
`rootlessport` helper has to bind whatever host port is on the left side
of `PublishPort=`, and only root can bind ports below 1024 by default —
publishing to `:514` directly fails with `rootlessport cannot expose
privileged port 514 ... bind: permission denied` unless the host's
`net.ipv4.ip_unprivileged_port_start` sysctl is lowered. Rather than make
that host-wide change (and risk colliding with any other syslog daemon
already listening on the host's standard `:514`), devices' logging
destination just needs to be configured to point at **`:10514`** on the
Drawbridge host explicitly — see `logger -P 10514` below and the IOS-XE
example in [logging.md](logging.md).

No unit test covers the container/rsyslog wiring itself (infra, not logic)
— verify manually after bringing the pod up:

```bash
# TCP
logger -n <drawbridge-host> -P 10514 -T "smoke test tcp"
# UDP
logger -n <drawbridge-host> -P 10514 -d "smoke test udp"
```

Then confirm a `source: 'syslog'` row appears via
`GET /api/v1/device-logs` (requires login) — or, `podman logs
drawbridge-rsyslog` should show the line arrive and the `omhttp` action
succeed, and `podman logs drawbridge` should show clean startup, with no
supervisor in between, in both containers.

## SAML SSO

Optional — Drawbridge only enables the `/saml/login`, `/saml/metadata`, and
`/saml/acs` routes when `SAML_SETTINGS_PATH` points at a directory
containing a `settings.json` (python3-saml's own settings format: `sp`/`idp`
blocks with entity IDs, ACS/SSO URLs, and certs). Mount that directory the
same way `/app/scripts` is mounted; nothing is generated automatically the
way the self-signed TLS cert is, since SAML needs real coordination with an
IdP (entity ID and ACS URL registered on their side) — there's no meaningful
default to fall back to. If no `settings.json` is present, the three routes
return `404 saml_disabled` and local login is unaffected.

**Configuring it, end to end:**

1. Pick Drawbridge's public URL (`https://drawbridge.example.com`, whatever
   the operator's actual deployed hostname is — must be reachable by both
   the admin's browser and the IdP).
2. Register an SP with your IdP (Okta, Azure AD/Entra, Keycloak, etc.) using:
   - **Entity ID / Audience URI**: any stable URI you control, e.g.
     `https://drawbridge.example.com/saml/metadata`.
   - **ACS URL / Reply URL** (where the IdP POSTs the assertion back):
     `https://drawbridge.example.com/saml/acs`, binding `HTTP-POST`.
   The IdP will give you back its own SSO URL, entity ID, and signing
   certificate — you need all three for the next step. (Some IdPs let you
   skip this by having Drawbridge serve `GET /saml/metadata` for them to
   import instead — the routes work either way as long as `settings.json`
   already exists, so do step 3 first with an empty/dummy `idp` block if
   your IdP wants to import metadata rather than have you paste values in.)
3. On the host, create `~/.local/share/drawbridge/saml/settings.json`
   (or wherever `SAML_SETTINGS_PATH` points):
   ```json
   {
     "strict": true,
     "sp": {
       "entityId": "https://drawbridge.example.com/saml/metadata",
       "assertionConsumerService": {
         "url": "https://drawbridge.example.com/saml/acs",
         "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
       },
       "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
       "x509cert": "",
       "privateKey": ""
     },
     "idp": {
       "entityId": "<from your IdP>",
       "singleSignOnService": {
         "url": "<your IdP's SSO URL>",
         "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
       },
       "x509cert": "<your IdP's signing certificate, PEM body only>"
     }
   }
   ```
   `sp.x509cert`/`sp.privateKey` can stay empty unless you also want
   Drawbridge to sign its outgoing `AuthnRequest`s (most IdPs don't require
   this for SP-initiated login) — if you do, generate a key pair the same
   way `tls.py` does and paste the PEM bodies (no `BEGIN/END` lines,
   `x509cert`/`privateKey` want the base64 body only) in here.
4. Mount that directory into the container at `SAML_SETTINGS_PATH`
   (`/app/data/saml` by default) — same pattern as `/app/scripts`:
   ```ini
   Volume=%h/.local/share/drawbridge/saml:/app/data/saml:Z
   ```
   in the Quadlet unit, or `-v ~/.local/share/drawbridge/saml:/app/data/saml`
   for a plain `podman run`.
5. Restart Drawbridge. `curl https://drawbridge.example.com/saml/metadata`
   should now return SP metadata XML instead of `404 saml_disabled` — a
   quick way to confirm the mount and JSON are both valid before involving
   the IdP.
6. Link an operator to "Log in with SSO" on the login page (already wired
   to `GET /saml/login`), or send them the IdP's own app link. First
   successful login self-provisions a Drawbridge account with
   `role='operator'` — promote it to `admin` afterward via `PUT
   /api/users/<id>` if needed, since SAML carries no group-to-role mapping
   in this release.

A first-time SAML login self-provisions a Drawbridge `User` row keyed on the
assertion's issuer + NameID (`role='operator'` by default — SAML carries no
group-to-role mapping in this release). See
[authentication.md](authentication.md) for the auth-backend design this
plugs into.

## Development Setup

`./dev.sh` is the normal entry point: it builds `Containerfile.dev` (Python
+ Node + Playwright/Chromium, kept separate from the production
`Containerfile`) and runs it with the repo bind-mounted in, so no host
Python/Node install is required. Inside the container it starts Flask
(debug/reload) and the Vite dev server (HMR) together — see
[frontend.md](frontend.md) ("Development workflow"). Browse
`http://localhost:5173`; `dev.sh` publishes `5173` and `$DRAWBRIDGE_PORT`
(default `8080`) to the host. `frontend/node_modules` lives in a named
volume (`drawbridge-dev-node-modules`), not the bind mount, so `npm
install` output never lands in the host tree. Ctrl-C stops the container,
resets the dev SQLite database, and offers to run `pytest` and rebuild the
production image.

```bash
./dev.sh
```

For a real headless-browser check of frontend behavior (not just the API
response) against a running `dev.sh` session, see
`tests/browser-integration/README.md`.

To run things by hand instead (no container):

```bash
# Clone and set up a virtualenv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run Flask in dev mode
export FLASK_APP=drawbridge/main.py
export FLASK_DEBUG=1
export DATABASE_PATH=./dev-data/drawbridge.db
export FILES_PATH=./dev-data/files
export DRAWBRIDGE_PORT=8080
mkdir -p dev-data/files
flask run --port $DRAWBRIDGE_PORT

# Run tests
pytest

# Build the production container image (multi-stage: builds frontend/, then the Flask image)
podman build -t localhost/drawbridge:latest .

# Build for a specific arch other than the host's (e.g. targeting a Raspberry
# Pi from an amd64 dev machine) - requires qemu-user-static for the emulated
# arch to be installed on the build host
podman build --arch arm64 -t localhost/drawbridge:latest .

# Build and push a single multi-arch manifest covering both amd64 and arm64
podman manifest create drawbridge-manifest
podman build --arch amd64 --manifest drawbridge-manifest .
podman build --arch arm64 --manifest drawbridge-manifest .
podman manifest push drawbridge-manifest <registry>/drawbridge:latest
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DRAWBRIDGE_PORT` | `8080` | Port Gunicorn binds to (`drawbridge/gunicorn.conf.py`) in production, and the port `flask run --port` / `dev.sh` use in local dev. `frontend/vite.config.js`'s dev-server proxy reads the same variable so it targets the right backend port automatically. **Not** read by `kea/kea-dhcp4.conf`'s Option 67 URL or `scripts/ztp-base.py`'s own `DRAWBRIDGE_PORT` constant — those are static/device-side and must be updated by hand if this changes from its default (see [decisions.md](decisions.md)) |
| `DATABASE_PATH` | `/app/data/drawbridge.db` | SQLite database file path, or a full SQLAlchemy URL (e.g. `postgresql+psycopg://user:pass@host/dbname`) to use PostgreSQL instead — see [database.md](database.md) |
| `WORKERS` | `4` | Number of Gunicorn worker processes. Ignored (forced to `1`) when `DATABASE_PATH` resolves to SQLite — see [database.md](database.md) |
| `FILES_PATH` | `/app/files` | Root directory for managed files. Subdirectories `images/`, `configs/`, and `scripts/` are created automatically on startup and should each be bind-mounted to the host if granular control is needed |
| `LOG_LEVEL` | `INFO` | App logger verbosity (`TRACE`/`DEBUG`/`INFO`/`WARNING`/`ERROR`). `TRACE` is a custom level below `DEBUG` — every successful API response logs its message plus the acting username (or `anonymous`) and remote IP; off by default since it fires on routine reads (list devices, list sessions, etc.), not just writes. Forced to `DEBUG` under `app.testing` regardless of this value |
| `FLASK_DEBUG` | `0` | Set to `1` in local dev only, never in container |
| `SECRET_KEY` | none — required | Flask session signing key for Flask-Login; must be set explicitly in every environment |
| `SQLITE_BUSY_TIMEOUT_MS` | `1000` | Per-connection `PRAGMA busy_timeout` (SQLite only) — see [database.md](database.md) |
| `LOG_RETENTION_DAYS` | `30` | Seeds the `log_retention_days` DB setting on first run only; change the live value via `PUT /api/settings/log-retention` instead. Set to `indefinite` for no purging |
| `DEFAULT_IMAGE` | none | Seeds the `default_image` DB setting on first run if set. Used as the fallback image for newly registered devices that don't specify one. Change the live value via `PUT /api/settings/default-image` |
| `DEFAULT_CONFIG_FILE` | none | Seeds the `default_config_file` DB setting on first run if set. Used as the fallback config file for newly registered devices that don't specify one. Change the live value via `PUT /api/settings/default-config-file` |
| `DEFAULT_SCRIPT` | none | Seeds the `default_script` DB setting on first run if set. Used as the fallback ZTP script for newly registered devices that don't specify one. Change the live value via `PUT /api/settings/default-script` |
| `ADMIN_PASSWORD` | none — random password generated and printed once if unset | Sets the bootstrap admin's initial password on first run only, instead of a random one. Forces a password reset on first login (see [authentication.md](authentication.md), "Bootstrap admin password sources") — prefer `CREDENTIALS_DIRECTORY` below where possible, since this value has to persist in plaintext (a Quadlet unit, a `.env` file) for the container to read it on every restart |
| `CREDENTIALS_DIRECTORY` | none | Set automatically by systemd when a unit uses `LoadCredential=`/`SetCredential=`; not meant to be set by hand. If a credential named `admin_password` exists in this directory on first run, it seeds the bootstrap admin's password without forcing a reset — see below and [authentication.md](authentication.md) |
| `TLS_CERT_PATH` | `/app/data/tls/cert.pem` | Path to Drawbridge's TLS certificate. Self-signed and auto-generated here on first run if nothing exists at this path — mount your own cert to use it instead. See "TLS" above |
| `TLS_KEY_PATH` | `/app/data/tls/key.pem` | Path to Drawbridge's TLS private key. Same first-run-generation behavior as `TLS_CERT_PATH` |
| `TLS_DISABLED` | unset (TLS on) | **Local development only** — set to `1` to skip TLS and run plain HTTP. Never set in a deployed/Quadlet config. See "TLS" above |
| `SAML_SETTINGS_PATH` | `/app/data/saml` | Directory containing python3-saml's `settings.json`. SAML routes are disabled (404) unless a `settings.json` exists there — see "SAML SSO" above |

### Systemd credentials for the bootstrap admin password

`ADMIN_PASSWORD` is simple but has to sit in plaintext somewhere durable
(a Quadlet unit's `Environment=` line, a `.env` file) for the container to
read it on every restart. A systemd credential avoids that: the secret
lives in its own file (optionally encrypted at rest via
`systemd-creds encrypt`), is exposed to the service only via a private,
per-invocation `$CREDENTIALS_DIRECTORY`, and never shows up in
`systemctl show`, `podman inspect`, or a process's `/proc/*/environ`.

In a Quadlet `.container` unit:

```ini
[Service]
LoadCredential=admin_password:/path/to/admin-password-secret
```

Recent Podman/Quadlet versions forward loaded credentials straight into the
container and set `CREDENTIALS_DIRECTORY` accordingly with no extra
`Volume=`/`Environment=` wiring — confirm this against the Podman version
actually in use when this gets validated in the containerization phase
(Quadlet is out of scope for alpha sign-off — see `alpha.md`); older
versions may need the credentials directory bind-mounted and
`CREDENTIALS_DIRECTORY` set explicitly in `[Container]` instead. Because
this is the more secure delivery channel regardless of wiring details, a
password sourced this way does **not** force a reset on first login, unlike
`ADMIN_PASSWORD`.
