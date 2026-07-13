# Deployment

## Container

**Containerfile** builds `localhost/drawbridge:latest` as a multi-stage
build:
- Stage 1 (`node:22-slim`): builds the Vue frontend (`frontend/`) with
  `npm ci && npm run build`. See [frontend.md](frontend.md).
- Stage 2 (`python:3.12-slim`, the final image):
  - Non-root user `drawbridge` (UID 1000) created in image
  - `COPY --from=` pulls the built frontend assets from stage 1 into
    `drawbridge/static/` — Node never ships in the final image
  - Gunicorn as WSGI server, binding `0.0.0.0:$DRAWBRIDGE_PORT` (default
    `8080`, via `-c drawbridge/gunicorn.conf.py` on the `CMD` — Gunicorn
    does not discover a config file nested under a subdirectory on its own)
  - `/app/data` and `/app/files` are mount points — do not COPY content there
  - Root filesystem is read-only at runtime; `/tmp` and `/run` are tmpfs

**Quadlet** at `~/.config/containers/systemd/drawbridge.container` (as
`drawbridge` user). See `quadlet/drawbridge.container` in this repo.

Host directories must exist before starting:
```bash
sudo mkdir -p /srv/drawbridge/{data,files}
sudo chown -R drawbridge:drawbridge /srv/drawbridge
```

## Development Setup

```bash
# Clone and set up a virtualenv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run Flask in dev mode (no container needed)
export FLASK_APP=drawbridge/main.py
export FLASK_DEBUG=1
export DATABASE_PATH=./dev-data/drawbridge.db
export FILES_PATH=./dev-data/files
export DRAWBRIDGE_PORT=8080
mkdir -p dev-data/files
flask run --port $DRAWBRIDGE_PORT

# Run tests
pytest

# Build the container image (multi-stage: builds frontend/, then the Flask image)
podman build -t localhost/drawbridge:latest .
```

The backend dev server above is enough on its own for API/backend work. For
frontend work, run the Vite dev server alongside it instead of rebuilding
the container on every change — see [frontend.md](frontend.md)
("Development workflow").

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DRAWBRIDGE_PORT` | `8080` | Port Gunicorn binds to (`drawbridge/gunicorn.conf.py`) in production, and the port `flask run --port` / `dev.sh` use in local dev. `frontend/vite.config.js`'s dev-server proxy reads the same variable so it targets the right backend port automatically. **Not** read by `kea/kea-dhcp4.conf`'s Option 67 URL or `scripts/ztp-base.py`'s own `DRAWBRIDGE_PORT` constant — those are static/device-side and must be updated by hand if this changes from its default (see [decisions.md](decisions.md)) |
| `DATABASE_PATH` | `/app/data/drawbridge.db` | SQLite database file path, or a full SQLAlchemy URL (e.g. `postgresql+psycopg://user:pass@host/dbname`) to use PostgreSQL instead — see [database.md](database.md) |
| `WORKERS` | `4` | Number of Gunicorn worker processes. Ignored (forced to `1`) when `DATABASE_PATH` resolves to SQLite — see [database.md](database.md) |
| `FILES_PATH` | `/app/files` | Root directory for managed files. Subdirectories `images/`, `configs/`, and `scripts/` are created automatically on startup and should each be bind-mounted to the host if granular control is needed |
| `FLASK_DEBUG` | `0` | Set to `1` in local dev only, never in container |
| `SECRET_KEY` | none — required | Flask session signing key for Flask-Login; must be set explicitly in every environment |
| `SQLITE_BUSY_TIMEOUT_MS` | `1000` | Per-connection `PRAGMA busy_timeout` (SQLite only) — see [database.md](database.md) |
| `LOG_RETENTION_DAYS` | `30` | Seeds the `log_retention_days` DB setting on first run only; change the live value via `PUT /api/settings/log-retention` instead. Set to `indefinite` for no purging |
| `DEFAULT_IMAGE` | none | Seeds the `default_image` DB setting on first run if set. Used as the fallback image for newly registered devices that don't specify one. Change the live value via `PUT /api/settings/default-image` |
| `DEFAULT_CONFIG_FILE` | none | Seeds the `default_config_file` DB setting on first run if set. Used as the fallback config file for newly registered devices that don't specify one. Change the live value via `PUT /api/settings/default-config-file` |
| `DEFAULT_SCRIPT` | none | Seeds the `default_script` DB setting on first run if set. Used as the fallback ZTP script for newly registered devices that don't specify one. Change the live value via `PUT /api/settings/default-script` |
| `ADMIN_PASSWORD` | none — random password generated and printed once if unset | Sets the bootstrap admin's initial password on first run only, instead of a random one. Forces a password reset on first login (see [authentication.md](authentication.md), "Bootstrap admin password sources") — prefer `CREDENTIALS_DIRECTORY` below where possible, since this value has to persist in plaintext (a Quadlet unit, a `.env` file) for the container to read it on every restart |
| `CREDENTIALS_DIRECTORY` | none | Set automatically by systemd when a unit uses `LoadCredential=`/`SetCredential=`; not meant to be set by hand. If a credential named `admin_password` exists in this directory on first run, it seeds the bootstrap admin's password without forcing a reset — see below and [authentication.md](authentication.md) |

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
