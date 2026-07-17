"""Self-signed TLS cert generation for Drawbridge's own listener (beta.md
section 3 — Drawbridge terminates its own TLS rather than relying on a
reverse proxy). An operator who mounts a real cert/key pair at TLS_CERT_PATH/
TLS_KEY_PATH instead just works — ensure_cert() only generates one if
nothing is there yet.
"""
import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from drawbridge.db import _sqlite_lock

CERT_VALIDITY_DAYS = 3650


def ensure_cert(cert_path: str, key_path: str) -> None:
    """Generates a self-signed cert/key pair at the given paths if they
    don't already exist; no-op otherwise. Must run before Gunicorn's master
    binds the listening socket (see gunicorn.conf.py, called at module
    level). Reuses drawbridge/db.py's fcntl.flock bootstrap-lock pattern
    rather than inventing new machinery.
    """
    with _sqlite_lock(cert_path):
        if Path(cert_path).exists() and Path(key_path).exists():
            return

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'drawbridge')])
        now = datetime.datetime.now(datetime.timezone.utc)

        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
            .sign(key, hashes.SHA256())
        )

        Path(cert_path).parent.mkdir(parents=True, exist_ok=True)
        Path(key_path).parent.mkdir(parents=True, exist_ok=True)

        Path(cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        Path(key_path).write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
