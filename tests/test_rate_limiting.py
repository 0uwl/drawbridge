import pytest

from drawbridge import create_app
from drawbridge.auth import limiter

from tests.conftest import PASSWORD

BASE = '/api/v1'


@pytest.fixture()
def app(tmp_path):
    """Own app fixture: the shared conftest.py `app` fixture forces
    RATELIMIT_ENABLED off (via app.testing) so unrelated tests don't hit
    429s — this file needs it on to exercise the limiter itself."""
    app = create_app({
        'TESTING': True,
        'RATELIMIT_ENABLED': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge-test.db'),
        'FILES_PATH': str(tmp_path / 'files'),
    })
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _reset_limiter():
    # `limiter` is a module-level singleton; its in-memory storage persists
    # across create_app() calls within one pytest process.
    yield
    limiter.reset()


def test_login_returns_429_after_limit_exceeded(client):
    for _ in range(10):
        client.post(f'{BASE}/auth/login', json={'username': 'nobody', 'password': 'wrong'})
    response = client.post(f'{BASE}/auth/login', json={'username': 'nobody', 'password': 'wrong'})
    assert response.status_code == 429


def test_claim_returns_429_after_limit_exceeded(client):
    for _ in range(5):
        client.post(f'{BASE}/auth/claim', json={'username': 'nobody', 'password': 'whatever-123', 'token': 'x'})
    response = client.post(f'{BASE}/auth/claim', json={'username': 'nobody', 'password': 'whatever-123', 'token': 'x'})
    assert response.status_code == 429


def test_reset_password_returns_429_after_limit_exceeded(client):
    for _ in range(5):
        client.post(f'{BASE}/auth/reset-password', json={
            'username': 'nobody', 'current_password': 'x', 'new_password': 'whatever-123',
        })
    response = client.post(f'{BASE}/auth/reset-password', json={
        'username': 'nobody', 'current_password': 'x', 'new_password': 'whatever-123',
    })
    assert response.status_code == 429


def test_rate_limit_response_uses_standard_envelope(client):
    for _ in range(11):
        response = client.post(f'{BASE}/auth/login', json={'username': 'nobody', 'password': 'wrong'})
    assert response.status_code == 429
    body = response.get_json()
    assert body['success'] is False
    assert body['error'] == 'rate_limited'
