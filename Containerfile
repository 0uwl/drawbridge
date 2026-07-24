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

RUN useradd --no-log-init --uid 1000 --create-home drawbridge

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-build /drawbridge/static ./drawbridge/static
RUN chown -R drawbridge:drawbridge /app

USER drawbridge

ENV FLASK_ENV=production

# Informational only — doesn't bind anything itself. Gunicorn's actual bind
# is now an internal-only port, not this one — drawbridge-nginx (sibling
# image, same pod) terminates TLS on :8080 and proxies to Gunicorn instead
# (see v0-3-2.md); :8080 here is a legacy default kept for TLS_DISABLED
# mode (drawbridge/gunicorn.conf.py), where Gunicorn binds it directly.
# Device syslog collection (:10514) lives in the sibling drawbridge-rsyslog
# container (Containerfile.rsyslog, same pod) — see docs/logging.md.
EXPOSE 8080

# /app/data and /app/files are mount points (see docs/deployment.md) — do
# not bake content into the image; bind-mount them at runtime.
ENTRYPOINT ["gunicorn", "drawbridge:create_app()", "-c", "drawbridge/gunicorn.conf.py"]
