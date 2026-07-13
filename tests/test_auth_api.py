from werkzeug.security import check_password_hash

from drawbridge.db import get_session
from drawbridge.models import User

from tests.conftest import PASSWORD

BASE = '/api/v1'


# POST /api/v1/auth/login

def test_login_returns_200_and_user_payload_on_correct_credentials(client, user):
    response = client.post(f'{BASE}/auth/login', json={'username': user.username, 'password': PASSWORD})
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['username'] == user.username
    assert payload['role'] == 'operator'
    assert payload['auth_source'] == 'local'
    assert 'id' in payload


def test_login_returns_401_on_wrong_password(client, user):
    response = client.post(f'{BASE}/auth/login', json={'username': user.username, 'password': 'wrong'})
    assert response.status_code == 401
    assert response.get_json()['success'] is False


def test_login_returns_401_on_unknown_username(client):
    response = client.post(f'{BASE}/auth/login', json={'username': 'nobody', 'password': 'x'})
    assert response.status_code == 401


def test_login_returns_401_for_saml_only_account(client, saml_user):
    response = client.post(f'{BASE}/auth/login', json={'username': saml_user.username, 'password': 'anything'})
    assert response.status_code == 401


def test_login_returns_400_when_username_missing(client):
    response = client.post(f'{BASE}/auth/login', json={'password': PASSWORD})
    assert response.status_code == 400


def test_login_returns_400_when_password_missing(client):
    response = client.post(f'{BASE}/auth/login', json={'username': 'operator'})
    assert response.status_code == 400


def test_login_returns_400_on_empty_body(client):
    response = client.post(f'{BASE}/auth/login', json={})
    assert response.status_code == 400


def test_login_withholds_session_when_must_reset_password_is_set(client, must_reset_user):
    response = client.post(f'{BASE}/auth/login', json={'username': must_reset_user.username, 'password': PASSWORD})
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload == {'must_reset_password': True, 'username': must_reset_user.username}

    # No session was actually established
    me_response = client.get(f'{BASE}/auth/me')
    assert me_response.status_code == 401


def test_login_returns_401_on_wrong_password_when_must_reset_password_is_set(client, must_reset_user):
    response = client.post(f'{BASE}/auth/login', json={'username': must_reset_user.username, 'password': 'wrong'})
    assert response.status_code == 401


def test_login_updates_last_login_at(app, client, user):
    client.post(f'{BASE}/auth/login', json={'username': user.username, 'password': PASSWORD})
    with app.app_context():
        updated = get_session().get(User, user.id)
        assert updated.last_login_at is not None


# POST /api/v1/auth/logout

def test_logout_returns_200_when_logged_in(logged_in_client):
    response = logged_in_client.post(f'{BASE}/auth/logout')
    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_logout_returns_401_when_not_logged_in(client):
    response = client.post(f'{BASE}/auth/logout')
    assert response.status_code == 401


def test_logout_ends_session(logged_in_client):
    logged_in_client.post(f'{BASE}/auth/logout')
    response = logged_in_client.get(f'{BASE}/auth/me')
    assert response.status_code == 401


# GET /api/v1/auth/me

def test_me_returns_current_user_when_logged_in(logged_in_client, user):
    response = logged_in_client.get(f'{BASE}/auth/me')
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['username'] == user.username


def test_me_returns_401_when_not_logged_in(client):
    response = client.get(f'{BASE}/auth/me')
    assert response.status_code == 401


# POST /api/v1/auth/claim

def test_claim_sets_password_on_unclaimed_local_account(app, client, unclaimed_user):
    response = client.post(f'{BASE}/auth/claim', json={'username': unclaimed_user.username, 'password': 'new-pass-123'})
    assert response.status_code == 200
    with app.app_context():
        updated = get_session().get(User, unclaimed_user.id)
        assert updated.password_hash is not None
        assert check_password_hash(updated.password_hash, 'new-pass-123')


def test_claim_allows_login_afterwards(client, unclaimed_user):
    client.post(f'{BASE}/auth/claim', json={'username': unclaimed_user.username, 'password': 'new-pass-123'})
    response = client.post(f'{BASE}/auth/login', json={'username': unclaimed_user.username, 'password': 'new-pass-123'})
    assert response.status_code == 200


def test_claim_returns_400_when_already_claimed(client, user):
    response = client.post(f'{BASE}/auth/claim', json={'username': user.username, 'password': 'whatever'})
    assert response.status_code == 400


def test_claim_returns_400_for_saml_only_account(client, saml_user):
    response = client.post(f'{BASE}/auth/claim', json={'username': saml_user.username, 'password': 'whatever'})
    assert response.status_code == 400


def test_claim_returns_400_for_unknown_username(client):
    response = client.post(f'{BASE}/auth/claim', json={'username': 'nobody', 'password': 'whatever'})
    assert response.status_code == 400


def test_claim_returns_400_when_password_missing(client, unclaimed_user):
    response = client.post(f'{BASE}/auth/claim', json={'username': unclaimed_user.username})
    assert response.status_code == 400


# POST /api/v1/auth/reset-password

def test_reset_password_succeeds_and_establishes_session(app, client, must_reset_user):
    response = client.post(f'{BASE}/auth/reset-password', json={
        'username': must_reset_user.username,
        'current_password': PASSWORD,
        'new_password': 'new-pass-123',
    })
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['username'] == must_reset_user.username

    with app.app_context():
        updated = get_session().get(User, must_reset_user.id)
        assert updated.must_reset_password is False
        assert check_password_hash(updated.password_hash, 'new-pass-123')

    # Session was established by the reset itself
    me_response = client.get(f'{BASE}/auth/me')
    assert me_response.status_code == 200


def test_reset_password_returns_400_with_wrong_current_password(must_reset_user, client):
    response = client.post(f'{BASE}/auth/reset-password', json={
        'username': must_reset_user.username,
        'current_password': 'wrong',
        'new_password': 'new-pass-123',
    })
    assert response.status_code == 400


def test_reset_password_returns_400_when_must_reset_password_is_not_set(client, user):
    response = client.post(f'{BASE}/auth/reset-password', json={
        'username': user.username,
        'current_password': PASSWORD,
        'new_password': 'new-pass-123',
    })
    assert response.status_code == 400


def test_reset_password_returns_400_for_unknown_username(client):
    response = client.post(f'{BASE}/auth/reset-password', json={
        'username': 'nobody',
        'current_password': 'whatever',
        'new_password': 'new-pass-123',
    })
    assert response.status_code == 400


def test_reset_password_returns_400_when_new_password_missing(must_reset_user, client):
    response = client.post(f'{BASE}/auth/reset-password', json={
        'username': must_reset_user.username,
        'current_password': PASSWORD,
    })
    assert response.status_code == 400


def test_reset_password_allows_subsequent_login(client, must_reset_user):
    client.post(f'{BASE}/auth/reset-password', json={
        'username': must_reset_user.username,
        'current_password': PASSWORD,
        'new_password': 'new-pass-123',
    })
    client.post(f'{BASE}/auth/logout')

    response = client.post(f'{BASE}/auth/login', json={'username': must_reset_user.username, 'password': 'new-pass-123'})
    assert response.status_code == 200
    assert response.get_json()['payload']['username'] == must_reset_user.username


# POST /api/v1/auth/change-password

def test_change_password_returns_401_when_not_logged_in(client):
    response = client.post(f'{BASE}/auth/change-password', json={'current_password': PASSWORD, 'new_password': 'new-pass'})
    assert response.status_code == 401


def test_change_password_succeeds_with_correct_current_password(app, logged_in_client, user):
    response = logged_in_client.post(f'{BASE}/auth/change-password', json={'current_password': PASSWORD, 'new_password': 'new-pass-123'})
    assert response.status_code == 200
    with app.app_context():
        updated = get_session().get(User, user.id)
        assert check_password_hash(updated.password_hash, 'new-pass-123')


def test_change_password_returns_400_with_wrong_current_password(logged_in_client):
    response = logged_in_client.post(f'{BASE}/auth/change-password', json={'current_password': 'wrong', 'new_password': 'new-pass-123'})
    assert response.status_code == 400


def test_change_password_returns_400_when_new_password_missing(logged_in_client):
    response = logged_in_client.post(f'{BASE}/auth/change-password', json={'current_password': PASSWORD})
    assert response.status_code == 400


def test_new_password_works_for_subsequent_login(client, logged_in_client, user):
    logged_in_client.post(f'{BASE}/auth/change-password', json={'current_password': PASSWORD, 'new_password': 'new-pass-123'})
    logged_in_client.post(f'{BASE}/auth/logout')
    response = client.post(f'{BASE}/auth/login', json={'username': user.username, 'password': 'new-pass-123'})
    assert response.status_code == 200
