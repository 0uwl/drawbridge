import pytest
from werkzeug.security import generate_password_hash

from drawbridge import create_app
from drawbridge.db import get_session
from drawbridge.models import User


PASSWORD = 'test-password-123'


@pytest.fixture()
def app(tmp_path):
    """Tests use a temporary SQLite file, never the
    production /app/data/drawbridge.db
    """
    config_dict = {
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge-test.db'),
        'FILES_PATH': str(tmp_path / 'files'),
    }

    app = create_app(config_dict)

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def session(app):
    """Request-scoped session, same lifecycle as a real request: opened on
    first use within the app context, closed when the context pops."""
    with app.app_context():
        yield get_session()


@pytest.fixture()
def user(app):
    with app.app_context():
        session = get_session()
        u = User(
            username='operator',
            role='operator',
            auth_source='local',
            password_hash=generate_password_hash(PASSWORD),
        )
        session.add(u)
        session.commit()
    return u


@pytest.fixture()
def admin_user(app):
    """A second admin distinct from the bootstrap 'admin' created by
    init_db(), so tests can log in as a known-password admin while still
    having a bootstrap admin around to delete/demote in last-admin-guard
    tests."""
    with app.app_context():
        session = get_session()
        u = User(
            username='test-admin',
            role='admin',
            auth_source='local',
            password_hash=generate_password_hash(PASSWORD),
        )
        session.add(u)
        session.commit()
    return u


CLAIM_TOKEN = 'test-claim-token-123'


@pytest.fixture()
def unclaimed_user(app):
    """An admin-created local account that hasn't claimed a password yet."""
    with app.app_context():
        session = get_session()
        u = User(
            username='new-operator',
            role='operator',
            auth_source='local',
            password_hash=None,
            claim_token=CLAIM_TOKEN,
        )
        session.add(u)
        session.commit()
    return u


@pytest.fixture()
def inactive_user(app):
    """A local account with is_active=False — nothing in the app sets this
    today, but login()/reset_password() must still refuse to establish a
    session for one (see docs/authentication.md)."""
    with app.app_context():
        session = get_session()
        u = User(
            username='inactive-operator',
            role='operator',
            auth_source='local',
            password_hash=generate_password_hash(PASSWORD),
            is_active=False,
        )
        session.add(u)
        session.commit()
    return u


@pytest.fixture()
def must_reset_user(app):
    """A local account whose password was seeded via ADMIN_PASSWORD (or
    equivalent) and hasn't completed its forced reset yet — see
    docs/authentication.md."""
    with app.app_context():
        session = get_session()
        u = User(
            username='must-reset-operator',
            role='operator',
            auth_source='local',
            password_hash=generate_password_hash(PASSWORD),
            must_reset_password=True,
        )
        session.add(u)
        session.commit()
    return u


@pytest.fixture()
def saml_user(app):
    with app.app_context():
        session = get_session()
        u = User(
            username='saml-operator',
            role='operator',
            auth_source='saml',
            password_hash=None,
        )
        session.add(u)
        session.commit()
    return u


@pytest.fixture()
def logged_in_client(app, user, client):
    client.post('/api/v1/auth/login', json={'username': user.username, 'password': PASSWORD})
    return client


@pytest.fixture()
def logged_in_admin_client(app, admin_user, client):
    client.post('/api/v1/auth/login', json={'username': admin_user.username, 'password': PASSWORD})
    return client
