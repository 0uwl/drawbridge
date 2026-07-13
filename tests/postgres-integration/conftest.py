"""Starts/stops a throwaway Postgres container for tests/postgres-integration/,
scoped to this directory only — the rest of the suite is untouched."""
import os
import shutil
import subprocess
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

IMAGE = 'docker.io/library/postgres:16-alpine'
READY_TIMEOUT_S = 30


@pytest.fixture(scope='session')
def pg_url():
    """Session-scoped: one container for the whole test run, torn down
    after. DRAWBRIDGE_TEST_POSTGRES_URL lets CI point this at a Postgres
    it already manages instead of spinning up a container here."""
    preexisting = os.environ.get('DRAWBRIDGE_TEST_POSTGRES_URL')
    if preexisting:
        yield preexisting
        return

    if shutil.which('podman') is None:
        pytest.skip('podman not found — install it or set DRAWBRIDGE_TEST_POSTGRES_URL to skip container management')

    container_name = f'drawbridge-pg-test-{uuid.uuid4().hex[:8]}'
    subprocess.run(
        [
            'podman', 'run', '--rm', '-d', '--name', container_name,
            '-e', 'POSTGRES_PASSWORD=test', '-e', 'POSTGRES_DB=drawbridge_test',
            '-p', '127.0.0.1::5432', IMAGE,
        ],
        check=True, capture_output=True, text=True,
    )
    try:
        url = f'postgresql+psycopg://postgres:test@127.0.0.1:{_discover_port(container_name)}/drawbridge_test'
        _wait_until_ready(url)
        yield url
    finally:
        subprocess.run(['podman', 'stop', container_name], capture_output=True)


def _discover_port(container_name: str, retries: int = 10) -> str:
    """podman assigns the host port at container start, but the mapping
    can take a beat to become queryable — retry rather than racing it."""
    for _ in range(retries):
        output = subprocess.run(
            ['podman', 'port', container_name, '5432'],
            capture_output=True, text=True,
        ).stdout.strip()
        if output:
            return output.rsplit(':', 1)[-1]
        time.sleep(0.5)
    raise RuntimeError(f'podman never reported a host port for container {container_name}')


def _wait_until_ready(url: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    last_error = None
    while time.monotonic() < deadline:
        try:
            engine = create_engine(url)
            with engine.connect():
                pass
            engine.dispose()
            return
        except OperationalError as e:
            last_error = e
            time.sleep(0.5)
    raise RuntimeError(f'Postgres container did not become ready within {READY_TIMEOUT_S}s: {last_error}')
