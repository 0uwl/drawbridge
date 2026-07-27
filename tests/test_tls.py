import shutil
import ssl
from pathlib import Path

from cryptography import x509

from drawbridge.tls import ensure_cert, sync_ztp_script_ca_cert


def test_ensure_cert_creates_both_files(tmp_path):
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'

    ensure_cert(str(cert_path), str(key_path))

    assert cert_path.is_file()
    assert key_path.is_file()


def test_ensure_cert_creates_missing_parent_dir(tmp_path):
    # Regression: /app/data exists (volume root) but /app/data/tls/ doesn't
    # on a fresh mount — ensure_cert() must create it, not assume it's there.
    cert_path = tmp_path / 'tls' / 'cert.pem'
    key_path = tmp_path / 'tls' / 'key.pem'

    ensure_cert(str(cert_path), str(key_path))

    assert cert_path.is_file()
    assert key_path.is_file()


def test_ensure_cert_is_a_noop_on_second_call(tmp_path):
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'

    ensure_cert(str(cert_path), str(key_path))
    first_cert = cert_path.read_bytes()

    ensure_cert(str(cert_path), str(key_path))

    assert cert_path.read_bytes() == first_cert


def test_ensure_cert_files_are_valid_pem(tmp_path):
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'

    ensure_cert(str(cert_path), str(key_path))

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))


def test_ensure_cert_includes_san_for_127_0_0_1_and_localhost(tmp_path):
    # drawbridge-nginx hostname-verifies https://127.0.0.1:8080 for its own
    # loopback testing and ignores a bare CN — regression coverage for that
    # SAN requirement (see v0-3-2.md).
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'

    ensure_cert(str(cert_path), str(key_path))

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert 'localhost' in san.get_values_for_type(x509.DNSName)
    assert str(san.get_values_for_type(x509.IPAddress)[0]) == '127.0.0.1'


def test_ensure_cert_extra_sans_are_added_alongside_loopback(tmp_path):
    # Regression: a real device dials the deployment's actual address, not
    # loopback — copy https://<address>:8080/... fails TLS hostname
    # verification without <address> in the SAN, even with the CA trusted
    # (see docs/deployment.md, "TLS").
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'

    ensure_cert(str(cert_path), str(key_path), extra_sans=['192.168.0.5', 'drawbridge.example'])

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ips = [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
    dns_names = san.get_values_for_type(x509.DNSName)
    assert '127.0.0.1' in ips
    assert '192.168.0.5' in ips
    assert 'localhost' in dns_names
    assert 'drawbridge.example' in dns_names


def test_ensure_cert_extra_sans_defaults_to_loopback_only(tmp_path):
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'

    ensure_cert(str(cert_path), str(key_path), extra_sans=[])

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ['127.0.0.1']
    assert list(san.get_values_for_type(x509.DNSName)) == ['localhost']


# sync_ztp_script_ca_cert

_SCRIPT_TEMPLATE = """\
DRAWBRIDGE_HOST = '192.168.100.1'

# --- DRAWBRIDGE_CA_CERT_PEM:BEGIN ---
DRAWBRIDGE_CA_CERT_PEM = None
# --- DRAWBRIDGE_CA_CERT_PEM:END ---

def main():
    pass
"""


def test_sync_ztp_script_ca_cert_is_a_noop_when_script_missing(tmp_path):
    cert_path = tmp_path / 'cert.pem'
    ensure_cert(str(cert_path), str(tmp_path / 'key.pem'))

    script_path = tmp_path / 'ztp_script.py'  # never created

    sync_ztp_script_ca_cert(str(cert_path), str(script_path))

    assert not script_path.exists()


def test_sync_ztp_script_ca_cert_is_a_noop_when_markers_missing(tmp_path):
    # An operator who removed the markers has opted out of the auto-sync -
    # see the comment in scripts/ztp_script.py.
    cert_path = tmp_path / 'cert.pem'
    ensure_cert(str(cert_path), str(tmp_path / 'key.pem'))

    script_path = tmp_path / 'ztp_script.py'
    unmarked = "DRAWBRIDGE_CA_CERT_PEM = None\n"
    script_path.write_text(unmarked)

    sync_ztp_script_ca_cert(str(cert_path), str(script_path))

    assert script_path.read_text() == unmarked


def test_sync_ztp_script_ca_cert_replaces_only_the_marked_block(tmp_path):
    cert_path = tmp_path / 'cert.pem'
    ensure_cert(str(cert_path), str(tmp_path / 'key.pem'))

    script_path = tmp_path / 'ztp_script.py'
    script_path.write_text(_SCRIPT_TEMPLATE)

    sync_ztp_script_ca_cert(str(cert_path), str(script_path))

    new_content = script_path.read_text()
    cert_pem = cert_path.read_text()
    assert f'DRAWBRIDGE_CA_CERT_PEM = """{cert_pem}"""' in new_content
    # Everything outside the marked block is untouched
    assert "DRAWBRIDGE_HOST = '192.168.100.1'" in new_content
    assert "def main():" in new_content
    assert new_content.count('DRAWBRIDGE_CA_CERT_PEM:BEGIN') == 1
    assert new_content.count('DRAWBRIDGE_CA_CERT_PEM:END') == 1


def test_sync_ztp_script_ca_cert_is_idempotent(tmp_path):
    cert_path = tmp_path / 'cert.pem'
    ensure_cert(str(cert_path), str(tmp_path / 'key.pem'))

    script_path = tmp_path / 'ztp_script.py'
    script_path.write_text(_SCRIPT_TEMPLATE)

    sync_ztp_script_ca_cert(str(cert_path), str(script_path))
    first_pass = script_path.read_text()

    sync_ztp_script_ca_cert(str(cert_path), str(script_path))
    second_pass = script_path.read_text()

    assert first_pass == second_pass


def test_sync_ztp_script_ca_cert_picks_up_a_regenerated_cert(tmp_path):
    # Mirrors docs/deployment.md's "delete cert.pem/key.pem to regenerate"
    # upgrade path - the ZTP script should pick up the new cert too.
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'
    ensure_cert(str(cert_path), str(key_path))

    script_path = tmp_path / 'ztp_script.py'
    script_path.write_text(_SCRIPT_TEMPLATE)
    sync_ztp_script_ca_cert(str(cert_path), str(script_path))
    first_cert_pem = cert_path.read_text()

    cert_path.unlink()
    key_path.unlink()
    ensure_cert(str(cert_path), str(key_path))
    second_cert_pem = cert_path.read_text()
    assert second_cert_pem != first_cert_pem  # sanity: actually a new cert

    sync_ztp_script_ca_cert(str(cert_path), str(script_path))

    new_content = script_path.read_text()
    assert f'DRAWBRIDGE_CA_CERT_PEM = """{second_cert_pem}"""' in new_content
    assert first_cert_pem not in new_content


def test_sync_ztp_script_ca_cert_matches_the_real_ztp_base_py(tmp_path):
    # Regression: confirms the markers/format this function expects
    # actually match what's checked into scripts/ztp_script.py, not just the
    # synthetic fixture used above.
    real_script = Path(__file__).parent.parent / 'scripts' / 'ztp_script.py'
    script_path = tmp_path / 'ztp_script.py'
    shutil.copy(real_script, script_path)

    cert_path = tmp_path / 'cert.pem'
    ensure_cert(str(cert_path), str(tmp_path / 'key.pem'))

    sync_ztp_script_ca_cert(str(cert_path), str(script_path))

    new_content = script_path.read_text()
    cert_pem = cert_path.read_text()
    assert f'DRAWBRIDGE_CA_CERT_PEM = """{cert_pem}"""' in new_content
