import pytest
from flask_login import login_required
from werkzeug.security import generate_password_hash

from drawbridge import create_app
from drawbridge.auth import admin_required, load_user
from drawbridge.db import get_session
from drawbridge.models import User
from drawbridge import queries


def test_load_user_returns_matching_user(app):
    with app.app_context():
        admin = queries.get_user_by_username(get_session(), 'admin')

        assert load_user(str(admin.id)) is admin


def test_load_user_returns_none_for_unknown_id(app):
    with app.app_context():
        assert load_user('999999') is None


def test_testing_app_gets_an_ephemeral_secret_key(app):
    assert app.config['SECRET_KEY']


def test_secret_key_required_outside_testing(tmp_path, monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)

    with pytest.raises(RuntimeError):
        create_app({'TESTING': False, 'DATABASE_PATH': str(tmp_path / 'drawbridge.db')})


def test_protected_route_without_session_returns_json_401(app):
    @app.route('/_test/protected')
    @login_required
    def _protected():
        return 'ok'

    client = app.test_client()
    response = client.get('/_test/protected')

    assert response.status_code == 401
    assert response.get_json()['success'] is False


# admin_required decorator — tests use a minimal /_test/admin route
# registered on the per-test app fixture so there are no cross-test route
# conflicts.

PASSWORD = 'test-password-123'


def _register_admin_test_route(app):
    @app.route('/_test/admin')
    @admin_required
    def _admin():
        return 'ok'
    return app.test_client()


def _create_and_login(app, client, *, role):
    with app.app_context():
        session = get_session()
        session.add(User(username=f'test-{role}', role=role, auth_source='local', password_hash=generate_password_hash(PASSWORD)))
        session.commit()
    client.post('/api/v1/auth/login', json={'username': f'test-{role}', 'password': PASSWORD})


def test_admin_required_returns_401_when_not_logged_in(app):
    client = _register_admin_test_route(app)
    response = client.get('/_test/admin')
    assert response.status_code == 401


def test_admin_required_returns_403_for_operator(app):
    client = _register_admin_test_route(app)
    _create_and_login(app, client, role='operator')
    response = client.get('/_test/admin')
    assert response.status_code == 403
    assert response.get_json()['success'] is False


def test_admin_required_allows_admin(app):
    client = _register_admin_test_route(app)
    _create_and_login(app, client, role='admin')
    response = client.get('/_test/admin')
    assert response.status_code == 200
