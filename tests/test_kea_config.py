"""Static contract tests for kea/*.conf — no Kea process involved. See
tests/kea-integration/README.md for the (documented, not-yet-built) tiers
that do require a running Kea instance."""
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

KEA_DIR = Path(__file__).parent.parent / 'kea'
SCRIPTS_DIR = Path(__file__).parent.parent / 'scripts'
APP_PORT = 8080  # DRAWBRIDGE_PORT's default (see docs/deployment.md) — this
# config's Option 67 URL is static and isn't read from that env var, so this
# constant would need updating by hand alongside kea-dhcp4.conf if the
# project's default port ever changed


def _load_jsonc(path: Path) -> dict:
    """Kea configs allow full-line `//` comments, which isn't strict JSON."""
    text = '\n'.join(
        line for line in path.read_text().splitlines()
        if not line.strip().startswith('//')
    )
    return json.loads(text)


@pytest.fixture(scope='module')
def dhcp4_conf():
    return _load_jsonc(KEA_DIR / 'kea-dhcp4.conf')['Dhcp4']


@pytest.fixture(scope='module')
def cisco_class(dhcp4_conf):
    return next(c for c in dhcp4_conf['client-classes'] if c['name'] == 'cisco-devices')


def test_kea_dhcp4_conf_is_valid_json(dhcp4_conf):
    assert 'client-classes' in dhcp4_conf


def test_kea_ctrl_agent_conf_is_valid_json():
    conf = _load_jsonc(KEA_DIR / 'kea-ctrl-agent.conf')['CtrlAgent']
    assert conf['control-sockets']['dhcp4']['socket-name']


def test_boot_file_name_basename_matches_ztp_script(cisco_class):
    boot_file = next(o for o in cisco_class['option-data'] if o['name'] == 'boot-file-name')
    url_path = urlparse(boot_file['data']).path
    assert Path(url_path).name == 'ztp-base.py'
    assert (SCRIPTS_DIR / 'ztp-base.py').is_file()


def test_boot_file_name_path_matches_files_download_route(cisco_class):
    # files.py's blueprint is registered under url_prefix='/files' (see
    # main.py) with a GET /scripts/<filename> route inside it — the full
    # device-facing path is /files/scripts/<filename>, not /scripts/<filename>.
    boot_file = next(o for o in cisco_class['option-data'] if o['name'] == 'boot-file-name')
    url_path = urlparse(boot_file['data']).path
    assert url_path == '/files/scripts/ztp-base.py'


def test_boot_file_name_uses_https(cisco_class):
    boot_file = next(o for o in cisco_class['option-data'] if o['name'] == 'boot-file-name')
    assert urlparse(boot_file['data']).scheme == 'https'


def test_boot_file_name_host_is_in_configured_subnet(dhcp4_conf, cisco_class):
    boot_file = next(o for o in cisco_class['option-data'] if o['name'] == 'boot-file-name')
    host = urlparse(boot_file['data']).hostname
    port = urlparse(boot_file['data']).port

    subnet = dhcp4_conf['subnet4'][0]['subnet']
    assert ipaddress.ip_address(host) in ipaddress.ip_network(subnet)
    assert port == APP_PORT


def test_known_network_vendor_only_references_defined_classes(dhcp4_conf):
    defined = {c['name'] for c in dhcp4_conf['client-classes']}
    composite = next(c for c in dhcp4_conf['client-classes'] if c['name'] == 'known-network-vendor')

    referenced = set(re.findall(r"member\('([^']+)'\)", composite['test']))
    assert referenced
    assert referenced <= defined - {'known-network-vendor'}
