#!/usr/bin/env bash
# Starts a local frontend dev session inside the dev container (built from
# Containerfile.dev): Flask backend (debug/reload) + Vite dev server (HMR),
# per docs/frontend.md and docs/deployment.md. No local Python/Node install
# required — everything runs in the container; only the repo itself and
# tests/dev-data are bind-mounted so edits on the host take effect live.
# Browse to http://localhost:5173 — Vite proxies /api, /files, /health to Flask.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

export DRAWBRIDGE_PORT="${DRAWBRIDGE_PORT:-8080}"
export DATABASE_PATH="${DATABASE_PATH:-./tests/dev-data/drawbridge.db}"
export FILES_PATH="${FILES_PATH:-./tests/dev-data/files}"
export KEA_CTRL_URL="${KEA_CTRL_URL:-http://localhost:8081}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-dev}"
export SECRET_KEY="${SECRET_KEY:-dev-only-insecure-secret-key}"
mkdir -p tests/dev-data

IMAGE="localhost/drawbridge-dev:latest"
CONTAINER="drawbridge-dev"
NODE_MODULES_VOLUME="drawbridge-dev-node-modules"

script_args=("$@")
cleaned_up=0
cleanup() {
    [ "$cleaned_up" -eq 1 ] && return
    cleaned_up=1
    echo "==> Stopping dev session"
    podman stop -t 5 "$CONTAINER" >/dev/null 2>&1 || true
    reset_dev_database
    clear_uploaded_files
}

reset_dev_database() {
    # SQLite only — never touch a real DATABASE_PATH URL (e.g.
    # postgresql://...). Dev data is explicitly regenerable (alpha.md's
    # resolved decisions #4), and the schema is still moving during active
    # alpha development — wiping it every session avoids a stale dev DB
    # missing a newly added column ("no such column: users.x") the next
    # time this script runs.
    if [[ "$DATABASE_PATH" != *"://"* ]]; then
        echo "==> Resetting dev database ($DATABASE_PATH)"
        rm -f "$DATABASE_PATH" "$DATABASE_PATH.bootstrap-lock" "$DATABASE_PATH-wal" "$DATABASE_PATH-shm"
    fi
}

clear_uploaded_files() {
    read -rp "Delete uploaded files from $FILES_PATH? [y/N] " delete
    if [[ "$delete" =~ ^[Yy]$ ]]; then
        echo "==> Clearing directory $FILES_PATH"
        rm -f "$FILES_PATH/*"
    fi
}

post_session_prompt() {
    echo
    read -rp "Run the test suite before exiting? [y/N] " run_tests
    [[ "$run_tests" =~ ^[Yy]$ ]] || return

    if pytest; then
        echo "==> Tests passed"
        read -rp "Build a new container image? [y/N] " build_image
        if [[ "$build_image" =~ ^[Yy]$ ]]; then
            podman build -t localhost/drawbridge:latest .
        fi
    else
        echo "==> Tests failed"
        read -rp "Start the dev session again? [y/N] " restart
        if [[ "$restart" =~ ^[Yy]$ ]]; then
            exec "$0" "${script_args[@]}"
        fi
    fi
}

trap cleanup EXIT TERM
trap 'cleanup; post_session_prompt' INT

echo "==> Building dev container image"
podman build -f Containerfile.dev -t "$IMAGE" .

echo "==> Starting dev container (Flask :$DRAWBRIDGE_PORT, Vite :5173)"
podman run --rm --name "$CONTAINER" \
    -p "${DRAWBRIDGE_PORT}:${DRAWBRIDGE_PORT}" \
    -p 5173:5173 \
    -v "$PWD:/app:Z" \
    -v "${NODE_MODULES_VOLUME}:/app/frontend/node_modules" \
    -e FLASK_APP=drawbridge/main.py \
    -e FLASK_DEBUG=1 \
    -e DRAWBRIDGE_PORT \
    -e DATABASE_PATH \
    -e FILES_PATH \
    -e KEA_CTRL_URL \
    -e ADMIN_PASSWORD \
    -e SECRET_KEY \
    "$IMAGE"
