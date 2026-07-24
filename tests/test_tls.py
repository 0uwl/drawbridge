import ssl

from cryptography import x509

from drawbridge.tls import ensure_cert


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
