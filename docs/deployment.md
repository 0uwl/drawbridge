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

**Requires Podman 5.0+.** The pod runs as four images, described by five
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
  - `ENTRYPOINT` runs `gunicorn` directly — no process supervisor (`-c
    drawbridge/gunicorn.conf.py` — Gunicorn does not discover a config file
    nested under a subdirectory on its own). Binds an internal-only
    `127.0.0.1:8078` by default — `drawbridge-nginx` terminates TLS and
    proxies to it (see "TLS" below); only under `TLS_DISABLED` does
    Gunicorn bind `0.0.0.0:$DRAWBRIDGE_PORT` (default `8080`) directly
  - `/app/data` and `/app/files` are mount points — do not COPY content there
  - Root filesystem is read-only at runtime; `/tmp`, `/run`, and
    `/home/drawbridge` are tmpfs — the last of those is for Gunicorn's own
    control-socket file (`~/.gunicorn/gunicorn.ctl`, unconfigured — see
    `gunicorn.conf.py`), not application data

**`Containerfile.rsyslog`** builds `localhost/drawbridge-rsyslog:latest` —
a separate, single-stage Alpine image (`apk add rsyslog rsyslog-http`),
`ENTRYPOINT` running `rsyslogd` directly, also no supervisor. Listens on
**:10514** inside the container, not the standard :514 — 514 is a
privileged port and both images run as non-root UID 1000 throughout (no
`CAP_NET_BIND_SERVICE`). See [logging.md](logging.md) for the full
container-layer and rsyslog-config design.

**`Containerfile.bootstrap`** builds `localhost/drawbridge-bootstrap:latest`
— a separate, single-stage `busybox:musl` image serving whatever's
bind-mounted at `/scripts` over plain, unauthenticated HTTP via busybox's
built-in `httpd` applet on **:8090**. This exists purely to break one
chicken-and-egg fetch: DHCP Option 67's `boot-file-name` fetch happens
before any ZTP script code has run on the device, so there's no way to
pre-establish trust for a self-signed cert ahead of it — confirmed against
real C9200CX hardware (see [decisions.md](decisions.md), "HTTPS cert trust
on C9200CX"). Not baked into the image: `scripts/ztp_script.py` has
deployment-specific constants (`DRAWBRIDGE_HOST`, `DRAWBRIDGE_CA_CERT_PEM`)
every operator must get right before it's usable against real hardware, so
`install.sh` seeds it once into `~/.local/share/drawbridge/files/scripts/ztp_script.py`
on first install (never overwriting an already-edited copy on a re-run) and
the container bind-mounts that directory read-only. `install.sh` sets
`DRAWBRIDGE_HOST` for you automatically, best-effort, from the IPv4 address
on whichever interface it resolved for Kea (falls back to the placeholder,
with a note to edit it by hand, if that can't be detected — e.g. no IP
assigned yet). `DRAWBRIDGE_CA_CERT_PEM` can't be set by `install.sh` the
same way — there's no cert to read yet at install time, since
`drawbridge/tls.py` only generates one the first time the `drawbridge`
container actually starts — but it doesn't need a manual edit either: the
`drawbridge` container patches it in automatically once it does start (see
"TLS" below). One script, no
per-device selection (see [decisions.md](decisions.md) "Facts-first
provisioning is deferred, not rejected" for why that was removed), but
still editable in place per deployment. Everything the script does after
that first fetch — phone-home, completion callback, log push, file
downloads — still goes over HTTPS against the main `drawbridge` container.
Deliberately its own container rather than folded into `drawbridge-nginx`
below (which could easily serve a static file too): ZTP script serving is
core, non-optional functionality, whereas `drawbridge-nginx` is meant to
be independently skippable — see "TLS" below.

**`Containerfile.nginx`** builds `localhost/drawbridge-nginx:latest` — a
separate, single-stage Alpine image (`apk add nginx nginx-mod-stream`),
`ENTRYPOINT` running `nginx -g "daemon off;"` directly, also no
supervisor. Terminates TLS on **:8080** (replacing Gunicorn's own — see
"TLS" below) and gives a plain-HTTP request against that same port a
clean response instead of a connection reset, using nginx's `stream` +
`ssl_preread` modules to detect the protocol before routing — see
[decisions.md](decisions.md) ("hardcoded 8080s") for the full design.
Everything it proxies goes to Gunicorn's internal `127.0.0.1:8078`.

**Quadlet**, at `~/.config/containers/systemd/`, run as whichever user
invokes it — there's no dedicated `drawbridge` system user:
- `quadlet/drawbridge.pod` — owns the pod's shared network namespace,
  `PublishPort=` (`8080` — owned by `drawbridge-nginx`, not Gunicorn, see
  "TLS" below — `8090`, `10514/udp`+`/tcp`, and `10515/tcp`), and
  `UserNS=keep-id` for member containers' bind mounts.
- `quadlet/drawbridge.container` — the app, `Pod=drawbridge.pod`; otherwise
  the same `Volume=`/`Environment=`/`ReadOnly=true`/`Tmpfs=` as before the
  pod split.
- `quadlet/drawbridge-rsyslog.container` — the syslog collector,
  `Pod=drawbridge.pod`, no volume mount at all (see [logging.md](logging.md)
  for why it doesn't need Drawbridge's TLS cert), `Restart=on-failure`
  independent of the app container — a crashed `drawbridge-rsyslog` no
  longer takes `drawbridge` down with it, unlike the old single-container/
  s6 setup.
- `quadlet/drawbridge-bootstrap.container` — the ZTP script server,
  `Pod=drawbridge.pod`, read-only bind mount of `files/scripts` (seeded by
  `install.sh` — see above), `ReadOnly=true`, `Restart=on-failure`
  independent of the other containers.
- `quadlet/drawbridge-nginx.container` — the TLS-terminating reverse
  proxy, `Pod=drawbridge.pod`, read-only bind mount of `data/tls`,
  `ReadOnly=true`, `Tmpfs=/tmp` (nginx's pid file and proxy temp buffers —
  see the file's own comments for why not `/var/lib/nginx`),
  `Restart=on-failure` — independent of the other containers, and meant
  to be skippable: an operator bringing their own reverse proxy just
  doesn't start this one (see "TLS" below).

[install.sh](../install.sh) installs all five there automatically for the
user running the script (run it as yourself, not as root/via `sudo` — it
calls `sudo` itself only for the steps that need it); if a unit is already
present and differs, it prompts to back up the old one before overwriting
rather than silently skipping or clobbering it.

Drawbridge is expected to run as a rootless Podman pod with the same
permissions as the invoking user. The data directories used for the
containers must therefore be owned by that user. It's recommended to create a
folder under that user's own XDG data dir, not a root-owned path like `/srv`,
so no `sudo`/`chown` is needed.

`install.sh` creates `~/.local/share/drawbridge/{data,files}` itself (and
seeds `files/scripts/ztp_script.py` — see "Container" above) — nothing to
create by hand there. Installing the Quadlet units manually instead of via
`install.sh` skips that step, so create them yourself first in that case:
```bash
mkdir -p ~/.local/share/drawbridge/{data,files}
```
Either way, these are just the documented default location — move them
elsewhere afterward if you want, and update the installed Quadlet units'
`Volume=` lines to match.

Then, after editing `SECRET_KEY` (and `ADMIN_PASSWORD` or `LoadCredential=`)
in the installed `drawbridge.container` unit:
```bash
systemctl --user daemon-reload && systemctl --user start drawbridge-pod.service
```
Starting the pod service starts all member containers together.

**[uninstall.sh](../uninstall.sh)** reverses everything `install.sh` deploys
except the packages and the pulled images: stops and removes the pod and
its containers, deletes the five Quadlet unit files, deletes the `/app/data`
and `/app/files` host directories (database, TLS cert/key, uploaded
images/configs), and removes the Kea config under `/etc/kea` that
`install.sh` wrote there — `podman`, the Kea packages themselves, and
`ghcr.io/0uwl/drawbridge:latest`/`-rsyslog:latest`/`-bootstrap:latest`/`-nginx:latest`
in local podman storage are all left alone. Those host directories default to
`~/.local/share/drawbridge/{data,files}`, but since the `Volume=` lines
above are editable, `uninstall.sh` reads the *installed*
`drawbridge.container` unit's actual `Volume=` lines to find the real
paths before removing anything — it only falls back to the default if that
unit is already gone. `*.bak.*` backup files from earlier
installs/upgrades are left alone too. Prompts for confirmation and lists
exactly what it's about to remove first; `-y`/`--yes` skips the prompt for
scripted use. Meant to be run before `install.sh` when testing a new
version on the same host, so nothing from the previous version's database
schema, cert, or config lingers into the fresh install.

## TLS

Drawbridge terminates its own TLS by default, via `drawbridge-nginx`, not
Gunicorn directly (see "Container" above) — GUI and most device-facing
traffic (ZTP phone-home, provision-complete, file downloads) go through
nginx's HTTPS listener on `:8080`, which proxies to Gunicorn's
internal-only `127.0.0.1:8078`. Gunicorn used to wrap its own socket in
TLS; that meant a plain HTTP request against `:8080` got nothing but a
connection reset, with no way to redirect on a socket already committed to
a TLS handshake. nginx's `stream`+`ssl_preread` modules fix this properly:
a plain-HTTP request against `:8080` now gets a clean response telling the
client to use `https://` instead (see [decisions.md](decisions.md),
"hardcoded 8080s").

The one exception is the very first fetch, the ZTP script itself: DHCP
Option 67's `boot-file-name` fetch happens before any script code has run,
so there's no way to pre-establish cert trust ahead of it — that one
fetch goes to the separate, deliberately plain-HTTP `drawbridge-bootstrap`
container on `:8090` instead (see "Container" above and
[decisions.md](decisions.md), "HTTPS cert trust on C9200CX"). On first
run, if `TLS_CERT_PATH`/`TLS_KEY_PATH` don't already exist,
`drawbridge/tls.py` (still run from the `drawbridge` container, at
Gunicorn startup) generates a self-signed cert/key pair there; an operator
who mounts their own cert/key pair at those paths instead (e.g. a real
ACME-issued cert, or a shared org CA) has it used as-is — nothing is
overwritten if the files are already present. `drawbridge-nginx` reads
this same path read-only (`quadlet/drawbridge-nginx.container`) to do the
actual termination.

The generated cert includes a SAN (`127.0.0.1` + `localhost`), needed for
`drawbridge-nginx`'s own hostname-verification testing against
`https://127.0.0.1:8080` — a bare-CN cert fails that check even when
otherwise trusted. **Upgrading from a pre-pod install:** delete
`data/tls/cert.pem` and `data/tls/key.pem` so `ensure_cert()` regenerates
them with the SAN on next start; an existing no-SAN cert is not replaced
automatically.

**`127.0.0.1`/`localhost` alone is never enough for a real device.** A
device's `copy https://<address>:8080/...` does its own TLS hostname
verification against whatever `<address>` it actually dialed — separate
from, and in addition to, the CA trust `_ensure_c9200cx_trustpoint()`
establishes (see [decisions.md](decisions.md), "HTTPS cert trust on
C9200CX"). Trusting the CA does not make the handshake succeed if
`<address>` isn't also listed in the cert's SAN: that fails hostname
verification the same way a browser rejects a cert for the wrong domain,
and since it happens before any HTTP request is parsed, it produces no
entry in `drawbridge-nginx`'s access log to debug from — just an I/O error
on the device side. `TLS_SAN_IPS` (below) adds whatever address(es)
devices actually reach this deployment on to the generated cert's SAN;
`install.sh` best-effort seeds it from the same interface detection it
uses for `DRAWBRIDGE_HOST` (see "Container" above), but like that value it
only applies on a fresh install and only takes effect the next time
`ensure_cert()` actually generates a cert — set it (or add to it) by hand
and delete `data/tls/cert.pem`/`data/tls/key.pem` to pick up a change.

**`scripts/ztp_script.py`'s `DRAWBRIDGE_CA_CERT_PEM` is kept in sync
automatically** — `drawbridge/tls.py`'s `sync_ztp_script_ca_cert()` runs
right after `ensure_cert()` on every `drawbridge` container start (cheap
no-op if nothing changed) and rewrites just the block between the
`# --- DRAWBRIDGE_CA_CERT_PEM:BEGIN/END ---` markers in the bind-mounted
ZTP script (`~/.local/share/drawbridge/files/scripts/ztp_script.py`, the
same file `install.sh` seeds — see "Container" above) with whatever cert
is actually at `TLS_CERT_PATH`. This also means the "delete
`cert.pem`/`key.pem` to regenerate" step just above updates the ZTP script
for free on the next restart. An operator who wants to manage this
constant by hand instead (e.g. pointing it at an org CA rather than the
served leaf cert) can just delete the BEGIN/END markers from their copy of
the script — `sync_ztp_script_ca_cert()` skips any file that doesn't have
them.

An operator who wants a "real" ACME-issued cert, a WAF, or some other
reverse proxy in front instead of `drawbridge-nginx` can either (a) mount
that cert/key pair at `TLS_CERT_PATH`/`TLS_KEY_PATH` — `drawbridge-nginx`
uses it as-is, no other change needed — or (b) set `TLS_DISABLED=1` and
`systemctl --user disable --now drawbridge-nginx.service`, so Gunicorn
binds `DRAWBRIDGE_PORT` directly in plain HTTP and their own proxy
terminates TLS in front of it instead. ZTP devices always talk to
whichever listener is actually reachable on `DRAWBRIDGE_PORT` — Option
67's boot-file URL is unaffected either way, since it points at
`drawbridge-bootstrap` on a different port entirely.

**Device-side cert trust needs a matching CA cert mounted/embedded on the
device side too**, since a self-signed server cert isn't trusted by anything
out of the box. `scripts/ztp_script.py`'s `DRAWBRIDGE_CA_CERT_PEM` constant
(hand-maintained, same posture as `DRAWBRIDGE_HOST`) must be set to
Drawbridge's actual cert (or its issuing CA) before the script is used
against real hardware — see [decisions.md](decisions.md) for how this gets
consumed on each platform branch.

**`TLS_DISABLED`** skips TLS entirely and makes Gunicorn bind
`DRAWBRIDGE_PORT` directly, in plain HTTP — either for local development
(`flask run`/`dev.sh`, no trusted cert needed) or as the supported
bring-your-own-reverse-proxy production mode described above. Either way
it also relaxes `SESSION_COOKIE_SECURE` so login still works over plain
HTTP. If you set it for the bring-your-own-proxy case specifically, also
disable `drawbridge-nginx` (see above) — otherwise it stays running with
nothing to terminate TLS for.

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
| `DRAWBRIDGE_PORT` | `8080` | The port this deployment is reached on. Only directly controls Gunicorn's own bind when `TLS_DISABLED` is set (see "TLS" above); otherwise Gunicorn always binds a fixed internal-only port and this is just the value `drawbridge-nginx`'s conf and `drawbridge.pod`'s `PublishPort=` need to agree on by hand if changed. Also the port `flask run --port`/`dev.sh` use in local dev — `frontend/vite.config.js`'s dev-server proxy reads the same variable so it targets the right backend port automatically. **Not** read by `kea/kea-dhcp4.conf`'s Option 67 URL or `scripts/ztp_script.py`'s own `DRAWBRIDGE_PORT` constant — those are static/device-side and must be updated by hand if this changes from its default (see [decisions.md](decisions.md)) |
| `DATABASE_PATH` | `/app/data/drawbridge.db` | SQLite database file path, or a full SQLAlchemy URL (e.g. `postgresql+psycopg://user:pass@host/dbname`) to use PostgreSQL instead — see [database.md](database.md) |
| `WORKERS` | `4` | Number of Gunicorn worker processes. Ignored (forced to `1`) when `DATABASE_PATH` resolves to SQLite — see [database.md](database.md) |
| `FILES_PATH` | `/app/files` | Root directory for managed files. Subdirectories `images/` and `configs/` are created automatically on startup and should each be bind-mounted to the host if granular control is needed. The ZTP script itself is not managed here — see "Container" above, `drawbridge-bootstrap` |
| `LOG_LEVEL` | `INFO` | App logger verbosity (`TRACE`/`DEBUG`/`INFO`/`WARNING`/`ERROR`). `TRACE` is a custom level below `DEBUG` — every successful API response logs its message plus the acting username (or `anonymous`) and remote IP; off by default since it fires on routine reads (list devices, list sessions, etc.), not just writes. Forced to `DEBUG` under `app.testing` regardless of this value |
| `FLASK_DEBUG` | `0` | Set to `1` in local dev only, never in container |
| `SECRET_KEY` | none — required | Flask session signing key for Flask-Login; must be set explicitly in every environment |
| `SQLITE_BUSY_TIMEOUT_MS` | `1000` | Per-connection `PRAGMA busy_timeout` (SQLite only) — see [database.md](database.md) |
| `LOG_RETENTION_DAYS` | `30` | Seeds the `log_retention_days` DB setting on first run only; change the live value via `PUT /api/settings/log-retention` instead. Set to `indefinite` for no purging |
| `DEFAULT_IMAGE` | none | Seeds the `default_image` DB setting on first run if set. Used as the fallback image for newly registered devices that don't specify one. Change the live value via `PUT /api/settings/default-image` |
| `DEFAULT_CONFIG_FILE` | none | Seeds the `default_config_file` DB setting on first run if set. Used as the fallback config file for newly registered devices that don't specify one. Change the live value via `PUT /api/settings/default-config-file` |
| `ADMIN_PASSWORD` | none — random password generated and printed once if unset | Sets the bootstrap admin's initial password on first run only, instead of a random one. Forces a password reset on first login (see [authentication.md](authentication.md), "Bootstrap admin password sources") — prefer `CREDENTIALS_DIRECTORY` below where possible, since this value has to persist in plaintext (a Quadlet unit, a `.env` file) for the container to read it on every restart |
| `CREDENTIALS_DIRECTORY` | none | Set automatically by systemd when a unit uses `LoadCredential=`/`SetCredential=`; not meant to be set by hand. If a credential named `admin_password` exists in this directory on first run, it seeds the bootstrap admin's password without forcing a reset — see below and [authentication.md](authentication.md) |
| `TLS_CERT_PATH` | `/app/data/tls/cert.pem` | Path to Drawbridge's TLS certificate. Self-signed and auto-generated here on first run if nothing exists at this path — mount your own cert to use it instead. See "TLS" above |
| `TLS_KEY_PATH` | `/app/data/tls/key.pem` | Path to Drawbridge's TLS private key. Same first-run-generation behavior as `TLS_CERT_PATH` |
| `TLS_SAN_IPS` | none | Comma-separated IPs/hostnames to add to the auto-generated cert's SAN, alongside the always-present `127.0.0.1`/`localhost`. Needed for any real device — `copy https://<address>:8080/...` fails hostname verification without `<address>` listed here even when its CA is correctly trusted. Only read on cert generation (unused if `TLS_CERT_PATH`/`TLS_KEY_PATH` already point at an existing cert); `install.sh` best-effort seeds it like `DRAWBRIDGE_HOST`. See "TLS" above |
| `TLS_DISABLED` | unset (TLS on) | Set to `1` to skip TLS and run plain HTTP on `DRAWBRIDGE_PORT` directly — either local development, or a supported production mode for an operator bringing their own reverse proxy instead of `drawbridge-nginx` (also disable that unit in that case). See "TLS" above |
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
