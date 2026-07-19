"""SAML SP backend (beta.md section 4). Generic SP side only — validates
assertions from whatever IdP the operator configures; Drawbridge has no
opinion about IdP choice.

Config is a single mounted directory (SAML_SETTINGS_PATH, same convention
as /app/scripts) containing python3-saml's own settings.json — see
docs/deployment.md. SAML is optional: a deployment that doesn't mount a
settings.json there simply has SAML routes return 404, no separate feature
flag needed.
"""
from pathlib import Path
from urllib.parse import urlsplit

from onelogin.saml2.auth import OneLogin_Saml2_Auth


def _prepare_flask_request(request) -> dict:
    """python3-saml wants a plain dict describing the request, not a Flask
    Request object — this is the standard adapter shape from python3-saml's
    own Flask example."""
    url_data = urlsplit(request.url)
    return {
        'https': 'on' if request.scheme == 'https' else 'off',
        'http_host': request.host,
        'server_port': url_data.port,
        'script_name': request.path,
        'get_data': request.args.copy(),
        'post_data': request.form.copy(),
    }


class SamlAuthBackend:
    """Config is injected via the constructor (a settings directory path)
    rather than read from globals inside the class, so this stays testable
    against an arbitrary settings.json without touching app config."""
    source = 'saml'

    def __init__(self, settings_path: str):
        self._settings_path = settings_path

    @property
    def enabled(self) -> bool:
        return (Path(self._settings_path) / 'settings.json').exists()

    def _auth(self, request) -> OneLogin_Saml2_Auth:
        return OneLogin_Saml2_Auth(_prepare_flask_request(request), custom_base_path=self._settings_path)

    def login_redirect_url(self, request) -> str:
        return self._auth(request).login()

    def metadata(self, request) -> tuple[str, list[str]]:
        """Returns (metadata_xml, errors). Non-empty errors means the SP
        settings themselves are invalid — a 500, not a caller error."""
        settings = self._auth(request).get_settings()
        metadata = settings.get_sp_metadata()
        return metadata, settings.validate_metadata(metadata)

    def process_acs(self, request) -> tuple[str, str, dict] | None:
        """Validates the POSTed SAMLResponse. Returns (issuer, name_id,
        attributes) on success, None on any validation failure (expired,
        bad signature, wrong audience, etc.) — caller logs/rejects
        uniformly rather than branching on the specific reason."""
        auth = self._auth(request)
        try:
            auth.process_response()
        except Exception:
            # A malformed SAMLResponse (e.g. not valid base64) raises rather
            # than populating get_errors() — same 401 either way.
            return None
        if auth.get_errors() or not auth.is_authenticated():
            return None
        issuer = auth.get_settings().get_idp_data()['entityId']
        return issuer, auth.get_nameid(), auth.get_attributes()
