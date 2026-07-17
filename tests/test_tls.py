import ssl

from drawbridge.tls import ensure_cert


def test_ensure_cert_creates_both_files(tmp_path):
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'

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
