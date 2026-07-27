"""Self-signed TLS cert generation for Drawbridge's own listener (beta.md
section 3 — Drawbridge terminates its own TLS rather than relying on a
reverse proxy). An operator who mounts a real cert/key pair at TLS_CERT_PATH/
TLS_KEY_PATH instead just works — ensure_cert() only generates one if
nothing is there yet.
"""
import datetime
import ipaddress
import re
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


_ZTP_CA_CERT_BEGIN = '# --- DRAWBRIDGE_CA_CERT_PEM:BEGIN ---'
_ZTP_CA_CERT_END = '# --- DRAWBRIDGE_CA_CERT_PEM:END ---'


def sync_ztp_script_ca_cert(cert_path: str, ztp_script_path: str) -> None:
    """Keeps scripts/ztp_script.py's DRAWBRIDGE_CA_CERT_PEM constant in sync
    with whatever cert Drawbridge is actually serving (self-signed or
    operator-supplied at TLS_CERT_PATH — the device-side trust anchor has
    to match either way), so this doesn't need a manual copy-paste step
    after every ensure_cert() run. Only rewrites the block between the
    BEGIN/END markers in the ZTP script (see scripts/ztp_script.py) —
    everything else in the file, including any real provisioning logic an
    operator has added, is left untouched. Must run after ensure_cert() so
    the cert actually exists to read.

    No-op if the ZTP script doesn't exist yet (files/scripts is
    drawbridge-bootstrap's concern, not guaranteed to exist in every
    deployment or test) or no longer has the markers (an operator who
    removed them has opted out of this — see the comment in
    scripts/ztp_script.py).
    """
    script = Path(ztp_script_path)
    if not script.is_file():
        return

    content = script.read_text()
    pattern = re.compile(
        re.escape(_ZTP_CA_CERT_BEGIN) + r'.*?' + re.escape(_ZTP_CA_CERT_END),
        re.DOTALL,
    )
    if not pattern.search(content):
        return

    cert_pem = Path(cert_path).read_text()
    replacement = (
        f'{_ZTP_CA_CERT_BEGIN}\n'
        f'DRAWBRIDGE_CA_CERT_PEM = """{cert_pem}"""\n'
        f'{_ZTP_CA_CERT_END}'
    )

    # A callable replacement, not a plain string, so any backslash in
    # cert_pem (never expected in real base64 PEM data, but this writes
    # into a script that runs against real network devices, worth being
    # defensive) can't be misread as a regex backreference by re.sub.
    new_content = pattern.sub(lambda _match: replacement, content, count=1)
    if new_content != content:
        script.write_text(new_content)
