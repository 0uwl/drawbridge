"""Self-signed TLS cert generation for Drawbridge's own listener (beta.md
section 3 — Drawbridge terminates its own TLS rather than relying on a
reverse proxy). An operator who mounts a real cert/key pair at TLS_CERT_PATH/
TLS_KEY_PATH instead just works — ensure_cert() only generates one if
nothing is there yet.
"""
import datetime
import ipaddress
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
    # Must exist before _sqlite_lock() below, which opens a lock file
    # alongside cert_path — on a fresh volume mount, /app/data/tls/ doesn't
    # exist yet (unlike the DB lock's parent, the volume root itself).
    Path(cert_path).parent.mkdir(parents=True, exist_ok=True)
    Path(key_path).parent.mkdir(parents=True, exist_ok=True)

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
            .add_extension(
                # drawbridge-nginx (v0-3-2.md) does hostname verification
                # of its own against this cert for local/loopback testing
                # (e.g. curl https://127.0.0.1:8080) and ignores a bare
                # CN — needs a SAN. rsyslog's omhttp no longer connects
                # over HTTPS at all (see container/rsyslog-drawbridge.conf),
                # so this SAN's original justification moved, not away.
                x509.SubjectAlternativeName([
                    x509.DNSName('localhost'),
                    x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
                ]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        Path(cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        Path(key_path).write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
