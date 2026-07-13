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

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-build /drawbridge/static ./drawbridge/static
RUN chown -R drawbridge:drawbridge /app

USER drawbridge

ENV FLASK_ENV=production

# Informational only — doesn't bind anything itself. Gunicorn's actual bind
# port follows DRAWBRIDGE_PORT (drawbridge/gunicorn.conf.py, default 8080 to
# match here); update the port mapping at run time if that's overridden.
EXPOSE 8080

# /app/data and /app/scripts are mount points (see docs/deployment.md) — do
# not bake content into the image; bind-mount them at runtime.
CMD ["gunicorn", "drawbridge:create_app()", "-c", "drawbridge/gunicorn.conf.py"]
