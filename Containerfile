# Build the Vue SPA (frontend/) into static assets. Built here rather than
# on the host so the image doesn't depend on a local Node install — see
# docs/frontend.md.
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN useradd --uid 1000 --create-home drawbridge

# s6-overlay (process supervisor) + rsyslog (device syslog collection — see
# docs/logging.md / beta.md §5, "in-container rsyslog via s6-overlay, not a
# sidecar"). Installed as root, but s6-overlay never needs a root init phase
# here: rsyslog listens on unprivileged :10514 (host :514 is remapped at the
# Podman network layer, see quadlet/drawbridge.container), so the container
# stays USER drawbridge throughout — no CAP_NET_BIND_SERVICE, no root PID 1.
ARG S6_OVERLAY_VERSION=3.2.0.2
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl xz-utils rsyslog \
    && rm -rf /var/lib/apt/lists/* \
    && S6_ARCH=$(case "$TARGETARCH" in amd64) echo x86_64 ;; arm64) echo aarch64 ;; *) echo x86_64 ;; esac) \
    && curl -fsSL -o /tmp/s6-overlay-noarch.tar.xz "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" \
    && curl -fsSL -o /tmp/s6-overlay-arch.tar.xz "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${S6_ARCH}.tar.xz" \
    && tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-overlay-arch.tar.xz \
    && rm /tmp/s6-overlay-*.tar.xz

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-build /drawbridge/static ./drawbridge/static
RUN chown -R drawbridge:drawbridge /app

# s6 service definitions + rsyslog config stay root-owned/read-only —
# nothing writes into them at runtime, only into /run (tmpfs, see quadlet).
COPY container/s6-rc.d /etc/s6-overlay/s6-rc.d
COPY container/rsyslog-drawbridge.conf /etc/rsyslog-drawbridge.conf

USER drawbridge

ENV FLASK_ENV=production

# Informational only — doesn't bind anything itself. Gunicorn's actual bind
# port follows DRAWBRIDGE_PORT (drawbridge/gunicorn.conf.py, default 8080 to
# match here); update the port mapping at run time if that's overridden.
# rsyslog listens on :10514 inside the container; the standard syslog port
# 514 is remapped to it at the Quadlet/Podman network layer (see
# quadlet/drawbridge.container) since 514 is privileged and this image
# never runs as root.
EXPOSE 8080
EXPOSE 514/udp
EXPOSE 514/tcp

# /app/data and /app/scripts are mount points (see docs/deployment.md) — do
# not bake content into the image; bind-mount them at runtime.
ENTRYPOINT ["/init"]
