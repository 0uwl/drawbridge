#!/usr/bin/env bash
# Entrypoint for Containerfile.dev — runs inside the dev container, started
# by dev.sh. /app is the repo, bind-mounted at container start (after the
# image was built), so anything installed here at build time under /app
# would be shadowed; frontend deps are installed at runtime instead into
# frontend/node_modules, backed by a named volume (see dev.sh) so the
# install persists across restarts without touching the host tree.
set -euo pipefail
cd /app

if [ ! -x frontend/node_modules/.bin/vite ]; then
    echo "==> Installing frontend dependencies"
    (cd frontend && npm install)
fi

mkdir -p "$(dirname "$DATABASE_PATH")" "$FILES_PATH"

cleanup() {
    kill "$FLASK_PID" "$VITE_PID" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT TERM INT

echo "==> Starting Flask backend on 0.0.0.0:$DRAWBRIDGE_PORT"
flask run --host 0.0.0.0 --port "$DRAWBRIDGE_PORT" &
FLASK_PID=$!

echo "==> Starting Vite dev server on 0.0.0.0:5173"
(cd frontend && npm run dev -- --host 0.0.0.0) &
VITE_PID=$!

wait -n "$FLASK_PID" "$VITE_PID"
