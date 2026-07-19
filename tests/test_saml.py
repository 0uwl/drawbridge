"""Tests for drawbridge/saml.py and the /saml/* routes.

No real IdP exists to test against (generic SP side only — see beta.md
section 4), so these tests split into two groups:

- Real, unmocked python3-saml validation for everything that doesn't need a
  correctly-signed assertion: settings loading, the login redirect URL, SP
  metadata generation, and process_acs()'s failure paths (malformed input,
  well-formed-but-unsigned/invalid responses — python3-saml genuinely
  rejects these on its own).
- The success path (a validly-signed assertion) would require hand-building
  a full signed SAML Response, which mostly re-tests python3-saml's own
  crypto rather than Drawbridge's code. Instead, SamlAuthBackend.process_acs
  is monkeypatched at the class level to simulate a successful assertion,
  so what's actually exercised is Drawbridge's own glue: extracting
  (issuer, nameid, attributes) and turning that into a get_or_create_saml_user
  upsert + an established Flask-Login session.
"""
import base64
import json

import pytest

from drawbridge import create_app
from drawbridge.db import get_session
from drawbridge.models import User
from drawbridge.queries import get_user_by_username
from drawbridge.saml import SamlAuthBackend

MINIMAL_SETTINGS = {
    "strict": False,
    "debug": False,
    "sp": {
        "entityId": "https://drawbridge.example.test/saml/metadata",
        "assertionConsumerService": {
            "url": "https://drawbridge.example.test/saml/acs",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
        },
        "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        "x509cert": "",
        "privateKey": "",
    },
    "idp": {
        "entityId": "https://idp.example.test/metadata",
        "singleSignOnService": {
            "url": "https://idp.example.test/sso",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        },
        "x509cert": "",
    },
}


@pytest.fixture()
def settings_dir(tmp_path):
    d = tmp_path / 'saml-settings'
    d.mkdir()
    (d / 'settings.json').write_text(json.dumps(MINIMAL_SETTINGS))
    return d


@pytest.fixture()
def backend(settings_dir):
    return SamlAuthBackend(str(settings_dir))


class _FakeFlaskRequest:
    """Minimal stand-in for a Flask Request — SamlAuthBackend only reads
    these attributes (see _prepare_flask_request)."""
    def __init__(self, post_data=None):
        self.url = 'https://drawbridge.example.test/saml/acs'
        self.scheme = 'https'
        self.host = 'drawbridge.example.test'
        self.path = '/saml/acs'
        self.args = {}
        self.form = post_data or {}


# SamlAuthBackend — real python3-saml validation, no mocking

def test_enabled_true_when_settings_json_present(backend):
    assert backend.enabled is True


def test_enabled_false_without_settings_json(tmp_path):
    backend = SamlAuthBackend(str(tmp_path / 'nonexistent'))
    assert backend.enabled is False


def test_login_redirect_url_points_at_idp_sso_endpoint(backend):
    url = backend.login_redirect_url(_FakeFlaskRequest())
    assert url.startswith('https://idp.example.test/sso?')
    assert 'SAMLRequest=' in url


def test_metadata_returns_sp_entity_id_with_no_validation_errors(backend):
    metadata, errors = backend.metadata(_FakeFlaskRequest())
    assert errors == []
    assert 'https://drawbridge.example.test/saml/metadata' in metadata


def test_process_acs_returns_none_for_malformed_saml_response(backend):
    request = _FakeFlaskRequest(post_data={'SAMLResponse': 'not-valid-base64!!'})
    assert backend.process_acs(request) is None


def test_process_acs_returns_none_for_unsigned_bogus_response(backend):
    bogus_xml = b'<Response>not a real saml response</Response>'
    request = _FakeFlaskRequest(post_data={'SAMLResponse': base64.b64encode(bogus_xml).decode()})
    assert backend.process_acs(request) is None


def test_process_acs_returns_none_with_no_saml_response_at_all(backend):
    assert backend.process_acs(_FakeFlaskRequest()) is None


# /saml/* routes — disabled-by-default (no SAML_SETTINGS_PATH configured)

def test_saml_routes_404_when_saml_not_configured(tmp_path):
    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge-test.db'),
        'FILES_PATH': str(tmp_path / 'files'),
    })
    client = app.test_client()
    assert client.get('/saml/login').status_code == 404
    assert client.get('/saml/metadata').status_code == 404
    assert client.post('/saml/acs').status_code == 404


# /saml/* routes — SAML configured via a mounted settings.json

@pytest.fixture()
def app(tmp_path, settings_dir):
    config_dict = {
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge-test.db'),
        'FILES_PATH': str(tmp_path / 'files'),
        'SAML_SETTINGS_PATH': str(settings_dir),
    }
    return create_app(config_dict)


@pytest.fixture()
def client(app):
    return app.test_client()


def test_saml_login_route_redirects_to_idp(client):
    response = client.get('/saml/login')
    assert response.status_code == 302
    assert response.headers['Location'].startswith('https://idp.example.test/sso?')


def test_saml_metadata_route_returns_xml(client):
    response = client.get('/saml/metadata')
    assert response.status_code == 200
    assert response.mimetype == 'text/xml'
    assert b'https://drawbridge.example.test/saml/metadata' in response.data


def test_saml_acs_route_returns_401_on_invalid_response(client):
    response = client.post('/saml/acs', data={'SAMLResponse': 'garbage'})
    assert response.status_code == 401


def test_saml_acs_route_creates_user_and_establishes_session(app, client, monkeypatch):
    monkeypatch.setattr(
        SamlAuthBackend, 'process_acs',
        lambda self, request: (
            'https://idp.example.test/metadata', 'alice@example.test', {'email': ['alice@example.test']},
        ),
    )

    response = client.post('/saml/acs', data={'SAMLResponse': 'irrelevant-because-mocked'})
    assert response.status_code == 302

    with app.app_context():
        user = get_user_by_username(get_session(), 'saml:alice@example.test')
        assert user is not None
        assert user.auth_source == 'saml'
        assert user.saml_issuer == 'https://idp.example.test/metadata'
        assert user.saml_subject == 'alice@example.test'
        assert user.email == 'alice@example.test'

    me_response = client.get('/api/v1/auth/me')
    assert me_response.status_code == 200
    assert me_response.get_json()['payload']['auth_source'] == 'saml'


def test_saml_acs_route_reuses_existing_user_on_repeat_login(app, client, monkeypatch):
    monkeypatch.setattr(
        SamlAuthBackend, 'process_acs',
        lambda self, request: ('https://idp.example.test/metadata', 'bob@example.test', {}),
    )

    client.post('/saml/acs', data={'SAMLResponse': 'x'})
    client.post('/api/v1/auth/logout')
    client.post('/saml/acs', data={'SAMLResponse': 'x'})

    with app.app_context():
        from sqlalchemy import select
        matches = get_session().scalars(
            select(User).where(User.saml_subject == 'bob@example.test')
        ).all()
        assert len(matches) == 1


def test_saml_acs_route_rejects_when_backend_disabled(tmp_path):
    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(tmp_path / 'drawbridge-test.db'),
        'FILES_PATH': str(tmp_path / 'files'),
    })
    client = app.test_client()
    response = client.get('/saml/metadata')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'saml_disabled'
