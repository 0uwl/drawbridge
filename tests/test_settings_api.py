from drawbridge.db import get_session
from drawbridge.models import Setting, User
from drawbridge.queries import add_log_entry, get_user_by_username

BASE = '/api/v1'


def _make_sole_admin(app, admin_user):
    """Deletes the bootstrap 'admin' user so admin_user becomes the last
    remaining admin — needed to exercise the last-admin guard."""
    with app.app_context():
        session = get_session()
        bootstrap_admin = get_user_by_username(session, 'admin')
        session.delete(bootstrap_admin)
        session.commit()


# GET /api/v1/users

def test_list_users_returns_401_when_not_logged_in(client):
    response = client.get(f'{BASE}/users')
    assert response.status_code == 401


def test_list_users_returns_403_for_operator(logged_in_client):
    response = logged_in_client.get(f'{BASE}/users')
    assert response.status_code == 403


def test_list_users_returns_200_for_admin(logged_in_admin_client, user):
    response = logged_in_admin_client.get(f'{BASE}/users')
    assert response.status_code == 200
    usernames = {u['username'] for u in response.get_json()['payload']}
    assert user.username in usernames


# POST /api/v1/users

def test_create_user_returns_401_when_not_logged_in(client):
    response = client.post(f'{BASE}/users', json={'username': 'new-operator', 'role': 'operator'})
    assert response.status_code == 401


def test_create_user_returns_403_for_operator(logged_in_client):
    response = logged_in_client.post(f'{BASE}/users', json={'username': 'new-operator', 'role': 'operator'})
    assert response.status_code == 403


def test_create_user_returns_422_when_username_missing(logged_in_admin_client):
    response = logged_in_admin_client.post(f'{BASE}/users', json={'role': 'operator'})
    assert response.status_code == 422


def test_create_user_returns_422_for_invalid_role(logged_in_admin_client):
    response = logged_in_admin_client.post(f'{BASE}/users', json={'username': 'new-operator', 'role': 'superuser'})
    assert response.status_code == 422


def test_create_user_returns_200_and_no_password_set(app, logged_in_admin_client):
    response = logged_in_admin_client.post(f'{BASE}/users', json={'username': 'new-operator', 'role': 'operator'})
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['is_claimed'] is False

    with app.app_context():
        created = get_user_by_username(get_session(), 'new-operator')
        assert created.password_hash is None
        assert created.auth_source == 'local'
        assert payload['claim_token'] == created.claim_token
        assert created.claim_token


def test_list_users_never_exposes_claim_token(logged_in_admin_client, user):
    response = logged_in_admin_client.get(f'{BASE}/users')
    assert response.status_code == 200
    assert all('claim_token' not in u for u in response.get_json()['payload'])


# PUT /api/v1/users/<id>

def test_update_role_returns_401_when_not_logged_in(client, user):
    response = client.put(f'{BASE}/users/{user.id}', json={'role': 'admin'})
    assert response.status_code == 401


def test_update_role_returns_403_for_operator(logged_in_client, user):
    response = logged_in_client.put(f'{BASE}/users/{user.id}', json={'role': 'admin'})
    assert response.status_code == 403


def test_update_role_returns_404_when_not_found(logged_in_admin_client):
    response = logged_in_admin_client.put(f'{BASE}/users/999999', json={'role': 'admin'})
    assert response.status_code == 404


def test_update_role_returns_422_for_invalid_role(logged_in_admin_client, user):
    response = logged_in_admin_client.put(f'{BASE}/users/{user.id}', json={'role': 'superuser'})
    assert response.status_code == 422


def test_update_role_promotes_operator_to_admin(app, logged_in_admin_client, user):
    response = logged_in_admin_client.put(f'{BASE}/users/{user.id}', json={'role': 'admin'})
    assert response.status_code == 200
    with app.app_context():
        assert get_session().get(User, user.id).role == 'admin'


def test_update_role_returns_409_when_demoting_last_admin(app, logged_in_admin_client, admin_user):
    _make_sole_admin(app, admin_user)
    response = logged_in_admin_client.put(f'{BASE}/users/{admin_user.id}', json={'role': 'operator'})
    assert response.status_code == 409
    assert response.get_json()['error'] == 'last_admin'


def test_update_role_allows_demotion_when_another_admin_exists(app, logged_in_admin_client, admin_user):
    # bootstrap 'admin' still exists, so admin_user isn't the last admin
    response = logged_in_admin_client.put(f'{BASE}/users/{admin_user.id}', json={'role': 'operator'})
    assert response.status_code == 200


# DELETE /api/v1/users/<id>

def test_delete_user_returns_401_when_not_logged_in(client, user):
    response = client.delete(f'{BASE}/users/{user.id}')
    assert response.status_code == 401


def test_delete_user_returns_403_for_operator(logged_in_client, user):
    response = logged_in_client.delete(f'{BASE}/users/{user.id}')
    assert response.status_code == 403


def test_delete_user_returns_404_when_not_found(logged_in_admin_client):
    response = logged_in_admin_client.delete(f'{BASE}/users/999999')
    assert response.status_code == 404


def test_delete_user_removes_operator_without_requiring_password(app, logged_in_admin_client, user):
    response = logged_in_admin_client.delete(f'{BASE}/users/{user.id}')
    assert response.status_code == 200
    with app.app_context():
        assert get_session().get(User, user.id) is None


def test_delete_user_returns_409_when_deleting_last_admin(app, logged_in_admin_client, admin_user):
    _make_sole_admin(app, admin_user)
    response = logged_in_admin_client.delete(f'{BASE}/users/{admin_user.id}')
    assert response.status_code == 409
    assert response.get_json()['error'] == 'last_admin'


def test_delete_user_allows_deleting_admin_when_another_admin_exists(app, logged_in_admin_client, admin_user):
    # bootstrap 'admin' still exists, so deleting admin_user is fine
    response = logged_in_admin_client.delete(f'{BASE}/users/{admin_user.id}')
    assert response.status_code == 200


# POST /api/v1/users/<id>/reset-password

def test_admin_reset_password_returns_401_when_not_logged_in(client, user):
    response = client.post(f'{BASE}/users/{user.id}/reset-password')
    assert response.status_code == 401


def test_admin_reset_password_returns_403_for_operator(logged_in_client, user):
    response = logged_in_client.post(f'{BASE}/users/{user.id}/reset-password')
    assert response.status_code == 403


def test_admin_reset_password_returns_404_when_not_found(logged_in_admin_client):
    response = logged_in_admin_client.post(f'{BASE}/users/999999/reset-password')
    assert response.status_code == 404


def test_admin_reset_password_returns_400_for_saml_account(logged_in_admin_client, saml_user):
    response = logged_in_admin_client.post(f'{BASE}/users/{saml_user.id}/reset-password')
    assert response.status_code == 400


def test_admin_reset_password_nulls_hash_and_issues_new_token(app, logged_in_admin_client, user):
    response = logged_in_admin_client.post(f'{BASE}/users/{user.id}/reset-password')
    assert response.status_code == 200
    payload = response.get_json()['payload']
    assert payload['claim_token']

    with app.app_context():
        updated = get_session().get(User, user.id)
        assert updated.password_hash is None
        assert updated.claim_token == payload['claim_token']


def test_admin_reset_password_allows_reclaim_via_claim_route(client, logged_in_admin_client, user):
    reset_response = logged_in_admin_client.post(f'{BASE}/users/{user.id}/reset-password')
    token = reset_response.get_json()['payload']['claim_token']

    claim_response = client.post(f'{BASE}/auth/claim', json={
        'username': user.username, 'password': 'new-pass-123', 'token': token,
    })
    assert claim_response.status_code == 200

    login_response = client.post(f'{BASE}/auth/login', json={'username': user.username, 'password': 'new-pass-123'})
    assert login_response.status_code == 200


# GET /api/v1/log

def test_list_log_returns_401_when_not_logged_in(client):
    response = client.get(f'{BASE}/log')
    assert response.status_code == 401


def test_list_log_returns_200_for_operator(app, logged_in_client):
    with app.app_context():
        add_log_entry(get_session(), serial='FJC2517X0AB', event='provision_complete')
        get_session().commit()

    response = logged_in_client.get(f'{BASE}/log')
    assert response.status_code == 200
    serials = {e['serial'] for e in response.get_json()['payload']}
    assert 'FJC2517X0AB' in serials


def test_list_log_returns_200_for_admin(logged_in_admin_client):
    response = logged_in_admin_client.get(f'{BASE}/log')
    assert response.status_code == 200
    assert response.get_json()['payload'] == []


# GET /api/v1/settings/log-retention

def test_get_log_retention_returns_401_when_not_logged_in(client):
    response = client.get(f'{BASE}/settings/log-retention')
    assert response.status_code == 401


def test_get_log_retention_returns_200_for_operator(logged_in_client):
    response = logged_in_client.get(f'{BASE}/settings/log-retention')
    assert response.status_code == 200
    assert response.get_json()['payload']['log_retention_days'] == '30'


# PUT /api/v1/settings/log-retention

def test_put_log_retention_returns_401_when_not_logged_in(client):
    response = client.put(f'{BASE}/settings/log-retention', json={'log_retention_days': '60'})
    assert response.status_code == 401


def test_put_log_retention_returns_403_for_operator(logged_in_client):
    response = logged_in_client.put(f'{BASE}/settings/log-retention', json={'log_retention_days': '60'})
    assert response.status_code == 403


def test_put_log_retention_returns_422_for_negative_value(logged_in_admin_client):
    response = logged_in_admin_client.put(f'{BASE}/settings/log-retention', json={'log_retention_days': '-1'})
    assert response.status_code == 422


def test_put_log_retention_returns_422_for_non_numeric_value(logged_in_admin_client):
    response = logged_in_admin_client.put(f'{BASE}/settings/log-retention', json={'log_retention_days': 'forever'})
    assert response.status_code == 422


def test_put_log_retention_updates_value(app, logged_in_admin_client):
    response = logged_in_admin_client.put(f'{BASE}/settings/log-retention', json={'log_retention_days': '60'})
    assert response.status_code == 200
    with app.app_context():
        assert get_session().get(Setting, 'log_retention_days').value == '60'


def test_put_log_retention_accepts_indefinite(app, logged_in_admin_client):
    response = logged_in_admin_client.put(f'{BASE}/settings/log-retention', json={'log_retention_days': 'indefinite'})
    assert response.status_code == 200
    with app.app_context():
        assert get_session().get(Setting, 'log_retention_days').value == 'indefinite'
