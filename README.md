# Drawbridge

A hardened, classic Zero Touch Provisioning (ZTP) system for Cisco IOS XE
devices — without relying on Cisco's PKI/Secure ZTP infrastructure.

> **⚠️ Network isolation is strongly recommended.** Drawbridge's device
> phone-home call is necessarily unauthenticated (a device has no credential
> on first contact — see [decisions.md](docs/decisions.md), "No sZTP"), so
> anyone who can reach the provisioning VLAN can enumerate registered device
> serials. Drawbridge's allowlist, HTTPS delivery, and hash verification
> protect *what* gets provisioned; they cannot protect against an attacker
> already on that VLAN. Drawbridge does not enforce network isolation and
> will run without it, but doing so knowingly widens who can reach it beyond
> what its own protections cover. **Run it on a physically/logically
> isolated provisioning VLAN with switch/firewall controls (802.1X, port
> security, DHCP snooping) restricting who can attach to it.** See
> [docs/deployment.md](docs/deployment.md#network-isolation--strongly-recommended)
> before deploying.

## Why

Classic ZTP is simple but insecure. Cisco's Secure ZTP (RFC 8572) fixes that,
but requires trusting Cisco's MASA service and PKI as a third party —
incompatible with a fully airgapped deployment. Drawbridge takes a third
path: it hardens classic ZTP using infrastructure the organization already
controls.

- Script-side provisioning gate (device phones home with its own serial
  before any real provisioning happens; unregistered devices get nothing
  beyond a normal DHCP lease and the generic script)
- Serial number allowlisting
- HTTPS script delivery with server certificate validation inside the script
- SHA-256 hash verification of images and config payloads

Drawbridge is **not** an inventory management system — device serials/MACs
are only tracked for the brief window between registration and
provisioning, then dropped in favor of a retention-bounded provisioning log.

## Documentation

| Topic | Covers |
|---|---|
| [Architecture](docs/architecture.md) | What this is, why not sZTP, system diagram, DHCP flow, repo layout |
| [Web API](docs/api.md) | Flask endpoints, the `/api/provision-request` contract, auth requirements |
| [Database](docs/database.md) | SQLAlchemy schema, multi-worker SQLite concurrency, log retention |
| [Authentication](docs/authentication.md) | Flask-Login, password hashing, planned SAML SP integration |
| [Frontend](docs/frontend.md) | Vue/Vite admin UI, dev-server proxy workflow, how it's baked into the container |
| [Kea Configuration](docs/kea.md) | Control Agent, vanilla DHCPv4 config — no custom hook |
| [Deployment](docs/deployment.md) | Containerfile, Quadlet, dev setup, environment variables |
| [Testing](docs/testing.md) | Testing approach and key cases |
| [Decisions & Constraints](docs/decisions.md) | Design tradeoffs and the reasoning behind each |
| [Security FAQ](docs/security-faq.md) | Known attack vectors in plain terms, what's actually exploitable today, and how to protect a deployment |

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

See [docs/deployment.md](docs/deployment.md) for running the app locally,
building the container, and the full list of environment variables.

## Installation

`ghcr.io/0uwl/drawbridge:latest` is a multi-arch image (`amd64` and
`arm64`) — it runs as-is on a Raspberry Pi (64-bit Raspberry Pi OS) or any
other arm64/amd64 Ubuntu/Debian host; Podman/Docker pull the matching arch
automatically, no extra flags needed.

On an Ubuntu/Debian provisioning host, [install.sh](install.sh) installs
`podman` and Kea if either is missing, installs Drawbridge's `kea/*.conf`
into `/etc/kea`, installs the Quadlet unit to
`~/.config/containers/systemd/drawbridge.container` for the invoking user
(skipped if one is already there, so a previously-edited unit is never
overwritten), and pulls `ghcr.io/0uwl/drawbridge:latest`:

```bash
curl -fsSL https://raw.githubusercontent.com/0uwl/drawbridge/main/install.sh | sudo bash
```

Review [install.sh](install.sh) before running it. It makes system changes
(installs packages, writes `/etc/kea`, writes the Quadlet unit) as root. It
does not start the Drawbridge container itself — `SECRET_KEY` still needs
setting in the installed unit and the host data directories still need
creating; the script prints the exact commands at the end. See
[docs/deployment.md](docs/deployment.md) for details.

Equivalent from a repo checkout: `./install.sh`.

## Development

Don't `pip install` the `drawbridge` package itself — there's no
`[build-system]` in `pyproject.toml`, and the inner `drawbridge/` directory
isn't a valid setuptools project root on its own. Just run `pytest` from the
repo root; `pythonpath = ["."]` under `[tool.pytest.ini_options]` in
`pyproject.toml` puts the repo root on `sys.path` so `tests/conftest.py` can
`import drawbridge` without an install.

`dev.sh` starts a full frontend dev session (Flask backend + Vite
dev server with HMR) in one command — see [docs/frontend.md](docs/frontend.md)
for the manual two-terminal workflow it wraps.
