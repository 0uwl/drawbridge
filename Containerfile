# Build the Vue SPA (frontend/) into static assets. Built here rather than
# on the host so the image doesn't depend on a local Node install — see
# docs/frontend.md. --platform=$BUILDPLATFORM pins this stage to the build
# host's own architecture rather than the target one: the output is
# arch-independent JS/CSS, so a linux/arm64 image build doesn't need to run
# this stage under QEMU emulation.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Multi-arch (linux/amd64, linux/arm64 — e.g. Raspberry Pi 64-bit OS):
#   docker buildx build --platform linux/amd64,linux/arm64 -t <tag> --push .
# python:3.12-slim publishes both arches natively; this stage is left
# unpinned so buildx targets whichever platform is being built.
FROM python:3.12-slim AS final

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# s6-overlay v3 defaults to S6_KEEP_ENV=0 - it strips the container's
# runtime environment (everything from `podman run -e` / the Quadlet's
# Environment= lines, including SECRET_KEY with no fallback) before
# starting any s6-rc.d service, unless that service's run script uses the
# with-contenv shebang helper. Our run scripts are plain `#!/usr/bin/env
# bash`, so without this, every Environment= variable in
# quadlet/drawbridge.container silently never reaches gunicorn/log-poller
# at all - see docs/deployment.md.
ENV S6_KEEP_ENV=1

RUN useradd --no-log-init --uid 1000 --create-home drawbridge

# s6-overlay (process supervisor) + rsyslog (device syslog collection — see
# docs/logging.md / beta.md §5, "in-container rsyslog via s6-overlay, not a
# sidecar"). Installed as root, but s6-overlay never needs a root init phase
# here: rsyslog listens on unprivileged :10514, published on the host as
# :10514 too rather than remapped to :514 (see quadlet/drawbridge.container),
# so the container stays USER drawbridge throughout — no
# CAP_NET_BIND_SERVICE, no root PID 1.
ARG S6_OVERLAY_VERSION=3.2.0.2
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl xz-utils rsyslog \
    && rm -rf /var/lib/apt/lists/* \
    && case "$TARGETARCH" in \
         amd64) S6_ARCH=x86_64 ;; \
         arm64) S6_ARCH=aarch64 ;; \
         *) echo "Unsupported TARGETARCH: '$TARGETARCH' (expected amd64 or arm64)" >&2; exit 1 ;; \
       esac \
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
COPY container/rsyslog-drawbridge.conf /etc/rsyslog.d/drawbridge.conf

USER drawbridge

ENV FLASK_ENV=production

# Informational only — doesn't bind anything itself. Gunicorn's actual bind
# port follows DRAWBRIDGE_PORT (drawbridge/gunicorn.conf.py, default 8080 to
# match here); update the port mapping at run time if that's overridden.
EXPOSE 8080
EXPOSE 10514/udp
EXPOSE 10514/tcp

# /app/data and /app/scripts are mount points (see docs/deployment.md) — do
# not bake content into the image; bind-mount them at runtime.
ENTRYPOINT ["/init"]
